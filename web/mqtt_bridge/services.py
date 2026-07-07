import json
import logging
import re

import paho.mqtt.publish as mqtt_publish
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.conf import settings
from django.utils import timezone as dj_timezone

from devices.models import CAPABILITIES_RESPONSE_TIMEOUT, CommandLog, Device, DeviceStatusLog
from readings.models import SensorReading

logger = logging.getLogger(__name__)

MAX_PAYLOAD_SIZE = 10240  # 10 KB
MAX_METRIC_NAME_LEN = 64
SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
# Firmware version accepts semver-style tokens (digits, dots, hyphens, plus).
FW_VERSION_RE = re.compile(r"^[a-zA-Z0-9.\-+]+$")

# Aliases for compact metric names sent by firmware.
# Maps short/alternate names to canonical metric names used in the database.
METRIC_ALIASES = {
    "temp": "temperature",
    "humi": "humidity",
    "press": "pressure",
    "uv": "uv_index",
    "batv": "bat_voltage",
    "battery_v": "bat_voltage",
    "bat": "bat_percent",
    "battery_pct": "bat_percent",
    "light_lux": "lux",
    "wifi_rssi": "rssi",
}


def _is_safe_identifier(value: str) -> bool:
    """Reject identifiers containing MQTT wildcards (+, #) or path separators (/)."""
    return bool(SAFE_IDENTIFIER_RE.match(value)) and len(value) <= 128


def parse_topic(topic: str) -> tuple[str, str, str] | None:
    """Parse topic like 'thermo/device1/sensors' into (device_type, device_id, msg_type)."""
    parts = topic.split("/")
    if len(parts) != 3:
        return None
    device_type, device_id, msg_type = parts
    if not _is_safe_identifier(device_type) or not _is_safe_identifier(device_id):
        logger.warning("Rejected unsafe topic identifiers: %s", topic)
        return None
    return device_type, device_id, msg_type


def _mqtt_publish(topic: str, payload: str, retain: bool = False, qos: int = 0):
    """Publish a single MQTT message.

    qos=1 lets the broker queue the message for a device that is currently
    offline but holds a persistent session (deep-sleep nodes), delivering it
    on the next reconnect. Use qos=1 for commands to sleeping devices.
    """
    logger.info("MQTT >> %s %s (retain=%s qos=%s)", topic, payload, retain, qos)
    mqtt_publish.single(
        topic,
        payload=payload,
        qos=qos,
        hostname=settings.MQTT_HOST,
        port=settings.MQTT_PORT,
        auth={"username": settings.MQTT_USER, "password": settings.MQTT_PASSWORD},
        retain=retain,
    )


def request_capabilities(device: Device, sent_by=None, log_command: bool = False):
    """Send a request_capabilities command to a device via MQTT.

    When ``log_command`` is set, a pending CommandLog entry is created so the
    request's lifecycle (pending → success/timeout) is visible in the UI. The
    response arrives on the capabilities topic, not as an ack, so it is
    resolved by handle_capabilities_message / the capabilities timeout check.
    """
    topic = f"{device.device_type}/{device.device_id}/command"
    payload = json.dumps({"action": "request_capabilities"})
    try:
        # qos=1 so the broker queues it for deep-sleep devices (persistent
        # session) and delivers it on their next wake. retain=False: it is a
        # one-shot request, not a standing command. Idempotent if re-delivered.
        _mqtt_publish(topic, payload, retain=False, qos=1)
        device.capabilities_requested_at = dj_timezone.now()
        device.save(update_fields=["capabilities_requested_at"])
        if log_command:
            CommandLog.objects.create(
                device=device,
                command={"action": "request_capabilities"},
                sent_by=sent_by,
                status=CommandLog.STATUS_PENDING,
            )
    except Exception:
        logger.exception("MQTT >> %s -> publish failed", topic)


def _resolve_capability_requests(device, status, message="", when=None):
    """Resolve all pending request_capabilities command logs for a device.

    A single capabilities response (or timeout) satisfies every outstanding
    capability request, so they are resolved together.
    """
    pending = CommandLog.objects.filter(
        device=device,
        status=CommandLog.STATUS_PENDING,
        command__action="request_capabilities",
    )
    resolved = pending.update(
        status=status,
        response_message=(message or "")[:256],
        acked=status in CommandLog.TERMINAL_STATUSES,
        acked_at=when or dj_timezone.now(),
    )
    return resolved


def flush_pending_commands(device: Device):
    """Publish all unacked commands to a device that just woke up.

    Called when a sleeping device reconnects (e.g. battery-powered devices
    that only enable WiFi periodically). Commands are sent in chronological
    order so the device processes them in the same sequence they were issued.
    """
    pending = (
        CommandLog.objects
        .filter(device=device, acked=False)
        .order_by("sent_at")
    )
    if not pending.exists():
        return

    topic = f"{device.device_type}/{device.device_id}/command"
    count = 0
    for cmd in pending:
        try:
            _mqtt_publish(topic, json.dumps(cmd.command), retain=True)
            count += 1
        except Exception:
            logger.exception("MQTT >> %s -> flush failed for command #%d", topic, cmd.pk)
    if count:
        logger.info("flush %s/%s -> re-published %d pending command(s)", device.device_type, device.device_id, count)


def handle_sensor_message(device_type: str, device_id: str, payload: bytes):
    """Process a sensor reading message."""
    if len(payload) > MAX_PAYLOAD_SIZE:
        logger.warning("sensors %s/%s -> rejected (payload too large: %d bytes)", device_type, device_id, len(payload))
        return

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("sensors %s/%s -> rejected (invalid JSON)", device_type, device_id)
        return

    if not isinstance(data, dict):
        logger.warning("sensors %s/%s -> rejected (payload is not a JSON object)", device_type, device_id)
        return

    now = dj_timezone.now()

    # Auto-discovery: create device if needed
    device, created = Device.objects.get_or_create(
        device_id=device_id,
        defaults={"device_type": device_type},
    )
    if created:
        logger.info("sensors %s/%s -> new device discovered", device_type, device_id)

    # Detect reconnection (was offline) before updating last_seen
    was_online = device.is_online

    device.last_seen = now
    update_fields = ["last_seen"]

    # Clear alert when device resumes normal sensor publishing
    if device.alert_level:
        device.alert_level = ""
        device.alert_message = ""
        update_fields += ["alert_level", "alert_message"]
        DeviceStatusLog.objects.create(
            time=now, device=device,
            alert_level="", alert_message="",
        )
        logger.info("sensors %s/%s -> cleared alert (normal data received)", device_type, device_id)
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            "live_readings",
            {
                "type": "device_status",
                "status": {
                    "device_id": device_id,
                    "alert_level": "",
                    "alert_message": "",
                    "device_name": device.effective_name,
                },
            },
        )

    device.save(update_fields=update_fields)

    # On wake-up: flush pending commands, then request capabilities
    if created or not was_online:
        logger.info("sensors %s/%s -> device woke up (was_online=%s, created=%s)", device_type, device_id, was_online, created)
        flush_pending_commands(device)
        request_capabilities(device)
    elif device.capabilities_requested_at is not None:
        # Check for capabilities response timeout. Deep-sleep devices only see
        # a queued request on their next wake, so allow at least two publish
        # intervals before flagging a missing response.
        elapsed = (now - device.capabilities_requested_at).total_seconds()
        timeout = max(CAPABILITIES_RESPONSE_TIMEOUT, 2 * (device.publish_interval or 0))
        if elapsed > timeout:
            device.alert_level = "error"
            device.alert_message = "no_capabilities_response"
            device.capabilities_requested_at = None
            device.save(update_fields=["alert_level", "alert_message", "capabilities_requested_at"])
            DeviceStatusLog.objects.create(
                time=now, device=device,
                alert_level="error", alert_message="no_capabilities_response",
            )
            _resolve_capability_requests(
                device, CommandLog.STATUS_TIMEOUT,
                message=f"No response within {int(timeout)}s", when=now,
            )
            logger.warning("sensors %s/%s -> capabilities timeout (%.0fs elapsed)", device_type, device_id, elapsed)

    if not device.is_approved:
        logger.info("sensors %s/%s -> dropped (device not approved)", device_type, device_id)
        return

    # Insert readings
    readings = []
    channel_layer = get_channel_layer()

    for raw_metric, value in data.items():
        if not isinstance(raw_metric, str) or len(raw_metric) > MAX_METRIC_NAME_LEN or not SAFE_IDENTIFIER_RE.match(raw_metric):
            logger.debug("sensors %s/%s -> skipped invalid metric: %s", device_type, device_id, raw_metric)
            continue
        metric = METRIC_ALIASES.get(raw_metric, raw_metric)
        try:
            float_value = float(value)
        except (ValueError, TypeError):
            logger.debug("sensors %s/%s -> skipped non-numeric value for %s: %s", device_type, device_id, metric, value)
            continue

        readings.append(
            SensorReading(time=now, device_id=device_id, metric=metric, value=float_value)
        )

        # Push to WebSocket
        async_to_sync(channel_layer.group_send)(
            "live_readings",
            {
                "type": "sensor_reading",
                "reading": {
                    "device_id": device_id,
                    "device_type": device_type,
                    "metric": metric,
                    "value": float_value,
                    "time": now.isoformat(),
                    "device_name": device.effective_name,
                },
            },
        )

    if readings:
        SensorReading.objects.bulk_create(readings)
        stored = {r.metric: r.value for r in readings}
        logger.info("sensors %s/%s -> stored %d reading(s): %s", device_type, device_id, len(readings), stored)

        # Track latest battery state of charge for server-side low-battery alerts.
        if "bat_percent" in stored and stored["bat_percent"] != device.battery_percent:
            device.battery_percent = stored["bat_percent"]
            device.save(update_fields=["battery_percent"])


def handle_status_message(device_type: str, device_id: str, payload: bytes):
    """Process a device status (warning/error) message."""
    if len(payload) > MAX_PAYLOAD_SIZE:
        logger.warning("status %s/%s -> rejected (payload too large: %d bytes)", device_type, device_id, len(payload))
        return

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("status %s/%s -> rejected (invalid JSON)", device_type, device_id)
        return

    if not isinstance(data, dict):
        logger.warning("status %s/%s -> rejected (payload is not a JSON object)", device_type, device_id)
        return

    try:
        device = Device.objects.get(device_id=device_id)
    except Device.DoesNotExist:
        logger.warning("status %s/%s -> rejected (unknown device)", device_type, device_id)
        return

    level = data.get("level", "")
    if level not in ("", "ok", "warning", "error"):
        logger.warning("status %s/%s -> rejected (invalid level: %s)", device_type, device_id, level)
        return

    # "ok" clears the alert
    if level in ("", "ok"):
        device.alert_level = ""
        device.alert_message = ""
        logger.info("status %s/%s -> alert cleared", device_type, device_id)
    else:
        device.alert_level = level
        message = data.get("message", "")
        if isinstance(message, str):
            device.alert_message = message[:256]
        logger.info("status %s/%s -> alert set: level=%s message=%s", device_type, device_id, level, device.alert_message)

    device.last_seen = dj_timezone.now()
    device.save(update_fields=["alert_level", "alert_message", "last_seen"])

    DeviceStatusLog.objects.create(
        time=device.last_seen, device=device,
        alert_level=device.alert_level, alert_message=device.alert_message,
    )

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        "live_readings",
        {
            "type": "device_status",
            "status": {
                "device_id": device_id,
                "alert_level": device.alert_level,
                "alert_message": device.alert_message,
                "device_name": device.effective_name,
            },
        },
    )


def handle_capabilities_message(device_type: str, device_id: str, payload: bytes):
    """Process a capabilities response from a device."""
    if len(payload) > MAX_PAYLOAD_SIZE:
        logger.warning("capabilities %s/%s -> rejected (payload too large: %d bytes)", device_type, device_id, len(payload))
        return

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("capabilities %s/%s -> rejected (invalid JSON)", device_type, device_id)
        return

    if not isinstance(data, dict):
        logger.warning("capabilities %s/%s -> rejected (payload is not a JSON object)", device_type, device_id)
        return

    try:
        device = Device.objects.get(device_id=device_id)
    except Device.DoesNotExist:
        logger.warning("capabilities %s/%s -> rejected (unknown device)", device_type, device_id)
        return

    # Compact keys: "id" (chip serial), "hw" (hardware code), "fw" (firmware
    # version), "intrvl", "metrics" (name→unit dict), "cmds" (name→params dict)
    hardware_id = data.get("id", "")
    if isinstance(hardware_id, str) and len(hardware_id) <= 256:
        device.hardware_id = hardware_id

    # hw/fw are optional: devices on outdated firmware omit them, which the
    # server surfaces as a recommended firmware update (Device.needs_firmware_update).
    hw_code = data.get("hw", "")
    if isinstance(hw_code, str) and len(hw_code) <= 64 and (hw_code == "" or SAFE_IDENTIFIER_RE.match(hw_code)):
        device.hw_code = hw_code

    fw_version = data.get("fw", "")
    if isinstance(fw_version, str) and len(fw_version) <= 32 and (fw_version == "" or FW_VERSION_RE.match(fw_version)):
        device.fw_version = fw_version

    publish_interval = data.get("intrvl", 0)
    if isinstance(publish_interval, (int, float)) and 0 < publish_interval <= 86400:
        device.publish_interval = int(publish_interval)

    capabilities = {}

    # metrics: {"name": "unit", ...} — merged metrics + units
    if isinstance(data.get("metrics"), dict):
        capabilities["metrics"] = [
            k for k in data["metrics"]
            if isinstance(k, str) and SAFE_IDENTIFIER_RE.match(k)
        ]
        capabilities["units"] = {
            k: v for k, v in data["metrics"].items()
            if isinstance(k, str) and SAFE_IDENTIFIER_RE.match(k)
            and isinstance(v, str) and len(v) <= 16 and v
        }

    # cmds: {"name": [params], ...} — merged commands + command_params
    valid_param_types = {"number", "string", "boolean"}
    if isinstance(data.get("cmds"), dict):
        capabilities["commands"] = [
            k for k in data["cmds"]
            if isinstance(k, str) and SAFE_IDENTIFIER_RE.match(k)
        ]
        command_params = {}
        for cmd_name, params in data["cmds"].items():
            if not isinstance(cmd_name, str) or not SAFE_IDENTIFIER_RE.match(cmd_name):
                continue
            if not isinstance(params, list):
                continue
            valid_params = []
            for p in params:
                if (isinstance(p, dict)
                        and isinstance(p.get("n"), str)
                        and isinstance(p.get("t"), str)
                        and p["t"] in valid_param_types):
                    valid_params.append({"name": p["n"], "type": p["t"]})
            command_params[cmd_name] = valid_params
        capabilities["command_params"] = command_params

    device.capabilities = capabilities
    was_pending = device.capabilities_requested_at is not None
    device.capabilities_requested_at = None
    had_timeout_alert = device.alert_level == "error" and device.alert_message == "no_capabilities_response"
    if had_timeout_alert:
        device.alert_level = ""
        device.alert_message = ""
        DeviceStatusLog.objects.create(
            time=dj_timezone.now(), device=device,
            alert_level="", alert_message="",
        )
    logger.info(
        "capabilities %s/%s -> stored: id=%s hw=%s fw=%s interval=%s metrics=%s commands=%s (pending_request=%s, cleared_timeout=%s)",
        device_type, device_id,
        device.hardware_id, device.hw_code or "-", device.fw_version or "-",
        device.publish_interval,
        capabilities.get("metrics"), capabilities.get("commands"),
        was_pending, had_timeout_alert,
    )
    device.save(update_fields=[
        "hardware_id", "hw_code", "fw_version", "publish_interval", "capabilities",
        "capabilities_requested_at", "alert_level", "alert_message",
    ])

    # A capabilities response fulfils any outstanding request_capabilities.
    _resolve_capability_requests(device, CommandLog.STATUS_SUCCESS)


def handle_ack_message(device_type: str, device_id: str, payload: bytes):
    """Process a command acknowledgement from a device."""
    if len(payload) > MAX_PAYLOAD_SIZE:
        logger.warning("ack %s/%s -> rejected (payload too large: %d bytes)", device_type, device_id, len(payload))
        return

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("ack %s/%s -> rejected (invalid JSON)", device_type, device_id)
        return

    if not isinstance(data, dict):
        logger.warning("ack %s/%s -> rejected (payload is not a JSON object)", device_type, device_id)
        return

    try:
        device = Device.objects.get(device_id=device_id)
    except Device.DoesNotExist:
        logger.warning("ack %s/%s -> rejected (unknown device)", device_type, device_id)
        return

    action = data.get("action", "")
    status = data.get("status", "")

    # Calibration response: {"temp": ..., "humi": ..., "press": ...}
    # Sent by the device in response to request_calibration, without action/status.
    if not action and "temp" in data:
        calibration = {}
        for key in ("temp", "humi", "press"):
            if key in data and isinstance(data[key], (int, float)):
                calibration[key] = round(float(data[key]), 2)
        if calibration:
            config = device.config or {}
            config["calibration"] = calibration
            device.config = config
            device.save(update_fields=["config"])
            logger.info("ack %s/%s -> stored calibration offsets: %s", device_type, device_id, calibration)
        return

    if not action or status not in ("ok", "error"):
        logger.warning("ack %s/%s -> rejected (invalid format: action=%s status=%s)", device_type, device_id, action, status)
        return

    # Find the most recent still-pending command matching this action
    cmd_log = (
        CommandLog.objects
        .filter(device=device, status=CommandLog.STATUS_PENDING, command__action=action)
        .order_by("-sent_at")
        .first()
    )
    if cmd_log:
        new_status = CommandLog.STATUS_SUCCESS if status == "ok" else CommandLog.STATUS_FAILED
        message = data.get("message", "")
        cmd_log.mark(new_status, message if isinstance(message, str) else "")
        delay = (cmd_log.acked_at - cmd_log.sent_at).total_seconds()
        logger.info("ack %s/%s -> matched command #%d (%s) status=%s delay=%.1fs", device_type, device_id, cmd_log.pk, action, new_status, delay)
    else:
        logger.warning("ack %s/%s -> no matching pending command for action=%s", device_type, device_id, action)

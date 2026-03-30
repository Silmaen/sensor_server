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


def _mqtt_publish(topic: str, payload: str, retain: bool = False):
    """Publish a single MQTT message."""
    logger.info("MQTT >> %s %s (retain=%s)", topic, payload, retain)
    mqtt_publish.single(
        topic,
        payload=payload,
        hostname=settings.MQTT_HOST,
        port=settings.MQTT_PORT,
        auth={"username": settings.MQTT_USER, "password": settings.MQTT_PASSWORD},
        retain=retain,
    )


def request_capabilities(device: Device):
    """Send a request_capabilities command to a device via MQTT."""
    topic = f"{device.device_type}/{device.device_id}/command"
    payload = json.dumps({"action": "request_capabilities"})
    try:
        _mqtt_publish(topic, payload, retain=False)
        device.capabilities_requested_at = dj_timezone.now()
        device.save(update_fields=["capabilities_requested_at"])
    except Exception:
        logger.exception("MQTT >> %s -> publish failed", topic)


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
        # Check for capabilities response timeout
        elapsed = (now - device.capabilities_requested_at).total_seconds()
        if elapsed > CAPABILITIES_RESPONSE_TIMEOUT:
            device.alert_level = "error"
            device.alert_message = "no_capabilities_response"
            device.capabilities_requested_at = None
            device.save(update_fields=["alert_level", "alert_message", "capabilities_requested_at"])
            DeviceStatusLog.objects.create(
                time=now, device=device,
                alert_level="error", alert_message="no_capabilities_response",
            )
            logger.warning("sensors %s/%s -> capabilities timeout (%.0fs elapsed)", device_type, device_id, elapsed)

    if not device.is_approved:
        logger.info("sensors %s/%s -> dropped (device not approved)", device_type, device_id)
        return

    # Insert readings
    readings = []
    channel_layer = get_channel_layer()

    for metric, value in data.items():
        if not isinstance(metric, str) or len(metric) > MAX_METRIC_NAME_LEN or not SAFE_IDENTIFIER_RE.match(metric):
            logger.debug("sensors %s/%s -> skipped invalid metric: %s", device_type, device_id, metric)
            continue
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

    # Compact keys: "id", "intrvl", "metrics" (name→unit dict), "cmds" (name→params dict)
    hardware_id = data.get("id", "")
    if isinstance(hardware_id, str) and len(hardware_id) <= 256:
        device.hardware_id = hardware_id

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
                        and isinstance(p.get("name"), str)
                        and isinstance(p.get("type"), str)
                        and p["type"] in valid_param_types):
                    valid_params.append({"name": p["name"], "type": p["type"]})
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
        "capabilities %s/%s -> stored: hw=%s interval=%s metrics=%s commands=%s (pending_request=%s, cleared_timeout=%s)",
        device_type, device_id,
        device.hardware_id, device.publish_interval,
        capabilities.get("metrics"), capabilities.get("commands"),
        was_pending, had_timeout_alert,
    )
    device.save(update_fields=[
        "hardware_id", "publish_interval", "capabilities",
        "capabilities_requested_at", "alert_level", "alert_message",
    ])


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

    if not action or status not in ("ok", "error"):
        logger.warning("ack %s/%s -> rejected (invalid format: action=%s status=%s)", device_type, device_id, action, status)
        return

    # Find the most recent unacked command matching this action
    cmd_log = (
        CommandLog.objects
        .filter(device=device, acked=False, command__action=action)
        .order_by("-sent_at")
        .first()
    )
    if cmd_log:
        cmd_log.acked = True
        cmd_log.acked_at = dj_timezone.now()
        cmd_log.save(update_fields=["acked", "acked_at"])
        delay = (cmd_log.acked_at - cmd_log.sent_at).total_seconds()
        logger.info("ack %s/%s -> matched command #%d (%s) status=%s delay=%.1fs", device_type, device_id, cmd_log.pk, action, status, delay)
    else:
        logger.warning("ack %s/%s -> no matching pending command for action=%s", device_type, device_id, action)

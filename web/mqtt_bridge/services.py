import json
import logging
import re

import paho.mqtt.publish as mqtt_publish
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.conf import settings
from django.utils import timezone as dj_timezone

from devices.models import (
    CAPABILITIES_RESPONSE_TIMEOUT,
    CommandLog,
    Device,
    DeviceDiagLog,
    DeviceStatusLog,
)
from ota.models import HardwareCode
from readings.models import SensorReading

logger = logging.getLogger(__name__)

MAX_PAYLOAD_SIZE = 10240  # 10 KB
MAX_METRIC_NAME_LEN = 64
SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
# Firmware version accepts semver-style tokens (digits, dots, hyphens, plus).
FW_VERSION_RE = re.compile(r"^[a-zA-Z0-9.\-+]+$")
# Hardware code: exactly 8 uppercase alphanumerics (see ota.models).
HW_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")
# Floor for the OTA confirmation window (scaled up by publish_interval): an OTA
# needs delivery (≤ interval), download, flash, reboot and a fresh capabilities
# cycle before success can be observed.
OTA_UPDATE_TIMEOUT = 600

# Server-generated alert raised when a device never answers a capabilities
# request. Unlike device-reported alerts (warning/error on the status topic),
# it is cleared server-side — either when capabilities finally arrive, or when
# the device resumes normal sensor publishing.
NO_CAPABILITIES_ALERT = "no_capabilities_response"

# Message a device uses for a firmware-reported low-battery alert on the status
# topic. The server reconciles it against its own battery reading: once a fresh
# bat_percent shows the battery is back to OK, the latched alert is cleared even
# if the device never publishes an explicit "ok" status (deep-sleep devices
# typically don't after a battery swap).
LOW_BATTERY_ALERT = "low_battery"

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


def request_commands(device: Device, sent_by=None):
    """Ask a device to publish its command list on the commands topic.

    Sent alongside request_capabilities: the two are split into separate messages
    to fit the firmware's 512-byte MQTT packet limit. qos=1 so the broker queues
    it for deep-sleep devices; the response arrives on the commands topic and is
    handled by handle_commands_message.
    """
    topic = f"{device.device_type}/{device.device_id}/command"
    payload = json.dumps({"action": "request_commands"})
    try:
        _mqtt_publish(topic, payload, retain=False, qos=1)
    except Exception:
        logger.exception("MQTT >> %s -> request_commands publish failed", topic)


def request_calibration(device: Device, sent_by=None):
    """Ask a device to publish its current calibration on the calibration topic.

    qos=1 (queued for deep-sleep devices), retain=False: this is a one-shot
    request, not a standing command — it must not linger retained on the broker.
    The response arrives on the calibration topic (handle_calibration_message).
    """
    topic = f"{device.device_type}/{device.device_id}/command"
    payload = json.dumps({"action": "request_calibration"})
    try:
        _mqtt_publish(topic, payload, retain=False, qos=1)
    except Exception:
        logger.exception("MQTT >> %s -> request_calibration publish failed", topic)


def request_status(device: Device, sent_by=None):
    """Ask a device to report its current health/alert state on demand (get_status).

    The device replies on its `status` topic with a normal status payload — an
    explicit "ok" is how the server clears a stale latched alert. qos=1 (queued
    for deep-sleep devices), retain=False: a one-shot request, not a standing
    command. Callers must gate on ``device.supports_diag``.
    """
    topic = f"{device.device_type}/{device.device_id}/command"
    payload = json.dumps({"action": "get_status"})
    try:
        _mqtt_publish(topic, payload, retain=False, qos=1)
    except Exception:
        logger.exception("MQTT >> %s -> get_status publish failed", topic)


def request_diag(device: Device, sent_by=None):
    """Ask a device to publish a diagnostics snapshot on demand (get_diag).

    The response arrives on the `diag` topic (handle_diag_message). qos=1 so it
    is queued for deep-sleep devices; retain=False (one-shot request). Callers
    must gate on ``device.supports_diag``.
    """
    topic = f"{device.device_type}/{device.device_id}/command"
    payload = json.dumps({"action": "get_diag"})
    try:
        _mqtt_publish(topic, payload, retain=False, qos=1)
    except Exception:
        logger.exception("MQTT >> %s -> get_diag publish failed", topic)


def _repush_calibration(device: Device):
    """Re-push the mirrored calibration to a device that reports an empty store.

    Triggered when a capabilities message carries ``cal: 0`` (store wiped: new
    board, factory reset, chip swap) and a server-side mirror exists. Published
    qos=1 so it is queued for deep-sleep devices. Not recorded as CommandLog
    entries: this is an automatic server-side recovery, not a user command.
    """
    mirror = device.calibration or {}
    topic = f"{device.device_type}/{device.device_id}/command"
    count = 0
    for key, value in mirror.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        # Offset keys (cal_temp/cal_humi/cal_press) go via set_offset on the bare
        # metric name; everything else (bat_divider, …) via set_calibration.
        if key.startswith("cal_"):
            cmd = {"action": "set_offset", "metric": key[4:], "value": value}
        else:
            cmd = {"action": "set_calibration", "key": key, "value": value}
        try:
            _mqtt_publish(topic, json.dumps(cmd), retain=False, qos=1)
            count += 1
        except Exception:
            logger.exception("MQTT >> %s -> calibration re-push failed (%s)", topic, key)
    if count:
        logger.info("re-push %s/%s -> %d calibration value(s) from mirror", device.device_type, device.device_id, count)


def _resolve_ota_updates(device):
    """Mark pending ota_update commands successful once the device runs the
    pushed version.

    OTA success is not acked (the device reboots); it is confirmed when the next
    capabilities message carries the target ``ver``. Called from the capabilities
    handler after ``fw_version`` has been updated.
    """
    pending = CommandLog.objects.filter(
        device=device,
        status=CommandLog.STATUS_PENDING,
        command__action="ota_update",
    )
    for cmd in pending:
        target = (cmd.command or {}).get("ver")
        if target and device.fw_version == target:
            cmd.mark(CommandLog.STATUS_SUCCESS, message=f"Running {target}")
            logger.info("ota_update %s/%s -> success (fw=%s)", device.device_type, device.device_id, target)


def _check_ota_timeouts(device, now):
    """Time out pending ota_update commands that were never confirmed.

    Success is confirmed by capabilities carrying the new fw and failure by an
    error ack. If neither happens within the window — device never returned, or a
    silent flash failure — the command is marked TIMEOUT. The window scales with
    the publish interval so deep-sleep devices are given enough wake cycles.
    """
    timeout = max(OTA_UPDATE_TIMEOUT, 3 * (device.publish_interval or 0))
    pending = CommandLog.objects.filter(
        device=device,
        status=CommandLog.STATUS_PENDING,
        command__action="ota_update",
    )
    for cmd in pending:
        elapsed = (now - cmd.sent_at).total_seconds()
        if elapsed > timeout:
            cmd.mark(CommandLog.STATUS_TIMEOUT, message=f"No confirmation within {int(timeout)}s", when=now)
            logger.warning("ota_update %s/%s -> timeout (%.0fs elapsed)", device.device_type, device.device_id, elapsed)


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
    # ota_update is excluded: it is published QoS 1 (queued for the device's
    # persistent session) and must stay retain=false — re-sending it via the
    # retain=True flush would leave a stale retained image command on the broker.
    pending = (
        CommandLog.objects
        .filter(device=device, acked=False)
        .exclude(command__action="ota_update")
        .order_by("sent_at")
    )
    if not pending.exists():
        return

    topic = f"{device.device_type}/{device.device_id}/command"
    count = 0
    for cmd in pending:
        try:
            # retain=False, qos=1: delivered via the persistent-session queue, not
            # left retained (a retained command re-fires on every reconnect).
            _mqtt_publish(topic, json.dumps(cmd.command), retain=False, qos=1)
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

    # Clear the server-generated capabilities-timeout alert when the device
    # resumes normal sensor publishing. Device-reported alerts (warning/error
    # on the status topic, e.g. low_battery) are NOT cleared here: the device
    # re-asserts them every cycle, so clearing them on each sensor message would
    # make the alert flap (cleared then re-set) and pollute the status timeline.
    # Those are cleared only by the device sending an ok/empty status message.
    if device.alert_message == NO_CAPABILITIES_ALERT:
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

    # Fail pending OTA pushes that were never confirmed within the window.
    _check_ota_timeouts(device, now)

    # On wake-up: flush pending commands, then request capabilities
    if created or not was_online:
        logger.info("sensors %s/%s -> device woke up (was_online=%s, created=%s)", device_type, device_id, was_online, created)
        flush_pending_commands(device)
        request_capabilities(device)
        request_commands(device)
        # Resync latched health on wake-up for diag-capable devices: the
        # get_status reply (on the status topic) either clears a stale alert or
        # re-asserts a real one. Gated on supports_diag — older firmware, which
        # does not advertise get_status/get_diag, is never sent this command.
        if device.supports_diag:
            request_status(device)
    elif device.capabilities_requested_at is not None:
        # Check for capabilities response timeout. Deep-sleep devices only see
        # a queued request on their next wake, so allow at least two publish
        # intervals before flagging a missing response.
        elapsed = (now - device.capabilities_requested_at).total_seconds()
        timeout = max(CAPABILITIES_RESPONSE_TIMEOUT, 2 * (device.publish_interval or 0))
        if elapsed > timeout:
            device.alert_level = "error"
            device.alert_message = NO_CAPABILITIES_ALERT
            device.capabilities_requested_at = None
            device.save(update_fields=["alert_level", "alert_message", "capabilities_requested_at"])
            DeviceStatusLog.objects.create(
                time=now, device=device,
                alert_level="error", alert_message=NO_CAPABILITIES_ALERT,
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
        if "bat_percent" in stored:
            if stored["bat_percent"] != device.battery_percent:
                device.battery_percent = stored["bat_percent"]
                device.save(update_fields=["battery_percent"])

            # Reconcile a latched firmware-reported low_battery alert against the
            # current reading: once the battery is back to OK (e.g. after a swap),
            # clear it. Deep-sleep devices rarely publish an explicit "ok" status,
            # so without this the warning would stick forever. Checked on every
            # reading that carries bat_percent — NOT only when the value changes —
            # so a device that keeps reporting the same good level still recovers.
            if (
                device.alert_level == "warning"
                and device.alert_message == LOW_BATTERY_ALERT
                and device.battery_status == "ok"
            ):
                device.alert_level = ""
                device.alert_message = ""
                device.save(update_fields=["alert_level", "alert_message"])
                DeviceStatusLog.objects.create(
                    time=now, device=device,
                    alert_level="", alert_message="",
                )
                logger.info(
                    "sensors %s/%s -> cleared low_battery alert (battery back to %.0f%%)",
                    device_type, device_id, device.battery_percent,
                )
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
    # The reported hw code is resolved against the CI-fed registry; an unknown
    # or absent code leaves hardware_code NULL (the raw claim is not retained).
    hw_code = data.get("hw", "")
    if isinstance(hw_code, str) and HW_CODE_RE.match(hw_code):
        device.hardware_code = HardwareCode.objects.filter(pk=hw_code).first()
    else:
        device.hardware_code = None

    hw_rev = data.get("hwrev")
    if isinstance(hw_rev, int) and not isinstance(hw_rev, bool) and 0 <= hw_rev <= 65535:
        device.hw_rev = hw_rev

    ota = data.get("ota")
    if isinstance(ota, bool) or (isinstance(ota, int) and ota in (0, 1)):
        device.ota_capable = bool(ota)

    # cal: calibration store-empty flag. 0 → wiped store (new board / factory
    # reset / chip swap); the mirror, if any, is re-pushed after saving.
    cal = data.get("cal")
    store_empty = cal == 0 or cal is False

    fw_version = data.get("fw", "")
    if isinstance(fw_version, str) and len(fw_version) <= 32 and (fw_version == "" or FW_VERSION_RE.match(fw_version)):
        device.fw_version = fw_version

    publish_interval = data.get("intrvl", 0)
    if isinstance(publish_interval, (int, float)) and 0 < publish_interval <= 86400:
        device.publish_interval = int(publish_interval)

    # Preserve commands/command_params posted by the separate `commands` message
    # (this handler only refreshes identity + metrics; a capabilities refresh
    # must not wipe the command list).
    capabilities = device.capabilities or {}

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
    had_timeout_alert = device.alert_level == "error" and device.alert_message == NO_CAPABILITIES_ALERT
    if had_timeout_alert:
        device.alert_level = ""
        device.alert_message = ""
        DeviceStatusLog.objects.create(
            time=dj_timezone.now(), device=device,
            alert_level="", alert_message="",
        )
    logger.info(
        "capabilities %s/%s -> stored: id=%s hw=%s rev=%s ota=%s fw=%s interval=%s metrics=%s commands=%s (pending_request=%s, cleared_timeout=%s)",
        device_type, device_id,
        device.hardware_id, device.hardware_code_id or "-", device.hw_rev,
        device.ota_capable, device.fw_version or "-",
        device.publish_interval,
        capabilities.get("metrics"), capabilities.get("commands"),
        was_pending, had_timeout_alert,
    )
    device.save(update_fields=[
        "hardware_id", "hardware_code", "hw_rev", "ota_capable", "fw_version",
        "publish_interval", "capabilities", "capabilities_requested_at",
        "alert_level", "alert_message",
    ])

    # A capabilities response fulfils any outstanding request_capabilities.
    _resolve_capability_requests(device, CommandLog.STATUS_SUCCESS)

    # An OTA succeeds silently (the device reboots and does not ack): a
    # capabilities message carrying the pushed version confirms it.
    _resolve_ota_updates(device)

    # After a store wipe the device reports cal:0; re-push the mirrored
    # calibration (per device_id) so it is restored (docs/ota-server.md §5.1).
    if store_empty and device.calibration:
        _repush_calibration(device)


def handle_commands_message(device_type: str, device_id: str, payload: bytes):
    """Process a command-list response (commands topic, answer to request_commands).

    The advertised command list is stored inside ``Device.capabilities``
    (``commands`` / ``command_params``): ``commands`` cannot be a Device field
    because that name is the CommandLog reverse relation.
    """
    if len(payload) > MAX_PAYLOAD_SIZE:
        logger.warning("commands %s/%s -> rejected (payload too large: %d bytes)", device_type, device_id, len(payload))
        return

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("commands %s/%s -> rejected (invalid JSON)", device_type, device_id)
        return

    if not isinstance(data, dict):
        logger.warning("commands %s/%s -> rejected (payload is not a JSON object)", device_type, device_id)
        return

    try:
        device = Device.objects.get(device_id=device_id)
    except Device.DoesNotExist:
        logger.warning("commands %s/%s -> rejected (unknown device)", device_type, device_id)
        return

    commands = []
    if isinstance(data.get("commands"), list):
        commands = [
            c for c in data["commands"]
            if isinstance(c, str) and SAFE_IDENTIFIER_RE.match(c)
        ]

    # Wire: command_params: {"cmd": [{"n": ..., "t": ...}], ...} (compact keys).
    valid_param_types = {"number", "string", "boolean"}
    command_params = {}
    if isinstance(data.get("command_params"), dict):
        for cmd_name, params in data["command_params"].items():
            if not isinstance(cmd_name, str) or not SAFE_IDENTIFIER_RE.match(cmd_name):
                continue
            if not isinstance(params, list):
                continue
            valid_params = []
            for p in params:
                # Device sends compact keys n/t (protocol §5.2); store the
                # verbose form used internally / by the UI.
                if (isinstance(p, dict)
                        and isinstance(p.get("n"), str)
                        and isinstance(p.get("t"), str)
                        and p["t"] in valid_param_types):
                    valid_params.append({"name": p["n"], "type": p["t"]})
            command_params[cmd_name] = valid_params

    capabilities = device.capabilities or {}
    capabilities["commands"] = commands
    capabilities["command_params"] = command_params
    device.capabilities = capabilities
    device.last_seen = dj_timezone.now()
    device.save(update_fields=["capabilities", "last_seen"])
    logger.info("commands %s/%s -> stored %d command(s): %s", device_type, device_id, len(commands), commands)


def handle_calibration_message(device_type: str, device_id: str, payload: bytes):
    """Process a calibration report (calibration topic, answer to request_calibration).

    Stores the device's current calibration as the server-side mirror
    (Device.calibration), replacing any previous snapshot. Used to bootstrap the
    mirror from an already-tuned unit; the mirror is re-pushed after a store wipe.
    """
    if len(payload) > MAX_PAYLOAD_SIZE:
        logger.warning("calibration %s/%s -> rejected (payload too large: %d bytes)", device_type, device_id, len(payload))
        return

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("calibration %s/%s -> rejected (invalid JSON)", device_type, device_id)
        return

    if not isinstance(data, dict):
        logger.warning("calibration %s/%s -> rejected (payload is not a JSON object)", device_type, device_id)
        return

    try:
        device = Device.objects.get(device_id=device_id)
    except Device.DoesNotExist:
        logger.warning("calibration %s/%s -> rejected (unknown device)", device_type, device_id)
        return

    mirror = {}
    for key, value in data.items():
        if (isinstance(key, str) and len(key) <= MAX_METRIC_NAME_LEN
                and SAFE_IDENTIFIER_RE.match(key)
                and isinstance(value, (int, float)) and not isinstance(value, bool)):
            mirror[key] = round(float(value), 4)

    device.calibration = mirror
    device.last_seen = dj_timezone.now()
    device.save(update_fields=["calibration", "last_seen"])
    logger.info("calibration %s/%s -> mirror updated: %s", device_type, device_id, mirror)


def _diag_uint(value, maximum):
    """Coerce a diag numeric field to a bounded non-negative int, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    ivalue = int(value)
    if 0 <= ivalue <= maximum:
        return ivalue
    return None


def handle_diag_message(device_type: str, device_id: str, payload: bytes):
    """Process a diagnostics snapshot from a device (`diag` topic).

    The device publishes this when its health level reaches `warning`/`error`,
    or on demand as the reply to a `get_diag` command. The technical fields are
    stored as a DeviceDiagLog time series (device health view); the level/message
    are reflected onto the device alert exactly like a status message — a
    warning/error latches an alert, an ok/info clears it. See docs/diagnostics.md
    (firmware repo) and docs/mqtt-protocol.md.
    """
    if len(payload) > MAX_PAYLOAD_SIZE:
        logger.warning("diag %s/%s -> rejected (payload too large: %d bytes)", device_type, device_id, len(payload))
        return

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("diag %s/%s -> rejected (invalid JSON)", device_type, device_id)
        return

    if not isinstance(data, dict):
        logger.warning("diag %s/%s -> rejected (payload is not a JSON object)", device_type, device_id)
        return

    try:
        device = Device.objects.get(device_id=device_id)
    except Device.DoesNotExist:
        logger.warning("diag %s/%s -> rejected (unknown device)", device_type, device_id)
        return

    level = data.get("level", "ok")
    if level not in ("ok", "info", "warning", "error"):
        logger.warning("diag %s/%s -> rejected (invalid level: %s)", device_type, device_id, level)
        return
    message = data.get("message", "")
    message = message[:256] if isinstance(message, str) else ""

    now = dj_timezone.now()

    rssi = data.get("rssi")
    rssi = int(rssi) if isinstance(rssi, (int, float)) and not isinstance(rssi, bool) else None

    DeviceDiagLog.objects.create(
        time=now,
        device=device,
        level=level,
        message=message,
        reset_cause=_diag_uint(data.get("rst"), 255),
        boot=_diag_uint(data.get("boot"), 2**31 - 1),
        miss=_diag_uint(data.get("miss"), 2**31 - 1),
        wake_ms=_diag_uint(data.get("wake_ms"), 2**31 - 1),
        seq=_diag_uint(data.get("seq"), 2**31 - 1),
        pubfail=_diag_uint(data.get("pubfail"), 2**31 - 1),
        rssi=rssi,
        heap=_diag_uint(data.get("heap"), 2**31 - 1),
        battery_percent=_diag_uint(data.get("bat"), 100),
    )

    # Reflect the reported health onto the device alert, mirroring the status
    # handler: warning/error latch an alert; ok/info clear it. Only touch the
    # DeviceStatusLog / WebSocket layer when the alert actually changes, so a
    # repeated warning diag does not spam the status timeline (the firmware also
    # publishes a status message for errors, which would otherwise double up).
    alert_level = level if level in ("warning", "error") else ""
    alert_message = message if alert_level else ""
    changed = device.alert_level != alert_level or device.alert_message != alert_message
    device.alert_level = alert_level
    device.alert_message = alert_message
    device.last_seen = now
    device.save(update_fields=["alert_level", "alert_message", "last_seen"])

    logger.info(
        "diag %s/%s -> level=%s message=%s rst=%s miss=%s heap=%s rssi=%s (alert_changed=%s)",
        device_type, device_id, level, message,
        data.get("rst"), data.get("miss"), data.get("heap"), data.get("rssi"), changed,
    )

    if changed:
        DeviceStatusLog.objects.create(
            time=now, device=device,
            alert_level=alert_level, alert_message=alert_message,
        )
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            "live_readings",
            {
                "type": "device_status",
                "status": {
                    "device_id": device_id,
                    "alert_level": alert_level,
                    "alert_message": alert_message,
                    "device_name": device.effective_name,
                },
            },
        )


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

    # Legacy calibration response (old firmware): {"temp":..,"humi":..,"press":..}
    # sent as an ack without action/status. Merged into the unified calibration
    # mirror (Device.calibration) with the cal_ prefix, alongside the dedicated
    # calibration topic used by newer firmware.
    if not action and "temp" in data:
        updates = {}
        for key in ("temp", "humi", "press"):
            if key in data and isinstance(data[key], (int, float)) and not isinstance(data[key], bool):
                updates["cal_" + key] = round(float(data[key]), 2)
        if updates:
            mirror = device.calibration or {}
            mirror.update(updates)
            device.calibration = mirror
            device.save(update_fields=["calibration"])
            logger.info("ack %s/%s -> stored calibration offsets (mirror): %s", device_type, device_id, updates)
        return

    # OTA start: informational, published before flashing. Leave the ota_update
    # command pending — success (new fw in capabilities) or an error ack resolves it.
    if action == "ota_update" and status == "start":
        logger.info("ack %s/%s -> ota_update start (device flashing)", device_type, device_id)
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

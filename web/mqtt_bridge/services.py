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


def request_capabilities(device: Device):
    """Send a request_capabilities command to a device via MQTT."""
    topic = f"{device.device_type}/{device.device_id}/command"
    payload = json.dumps({"action": "request_capabilities"})
    try:
        mqtt_publish.single(
            topic,
            payload=payload,
            hostname=settings.MQTT_HOST,
            port=settings.MQTT_PORT,
            auth={"username": settings.MQTT_USER, "password": settings.MQTT_PASSWORD},
            retain=False,
        )
        device.capabilities_requested_at = dj_timezone.now()
        device.save(update_fields=["capabilities_requested_at"])
        logger.info("Requested capabilities from %s", device.device_id)
    except Exception:
        logger.exception("Failed to request capabilities from %s", device.device_id)


def handle_sensor_message(device_type: str, device_id: str, payload: bytes):
    """Process a sensor reading message."""
    if len(payload) > MAX_PAYLOAD_SIZE:
        logger.warning("Payload too large from %s/%s: %d bytes", device_type, device_id, len(payload))
        return

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Invalid JSON from %s/%s: %s", device_type, device_id, payload[:100])
        return

    if not isinstance(data, dict):
        logger.warning("Non-dict JSON from %s/%s", device_type, device_id)
        return

    now = dj_timezone.now()

    # Auto-discovery: create device if needed
    device, created = Device.objects.get_or_create(
        device_id=device_id,
        defaults={"device_type": device_type},
    )
    if created:
        logger.info("Auto-discovered device: %s (type=%s)", device_id, device_type)

    # Detect reconnection (was offline) before updating last_seen
    was_online = device.is_online

    device.last_seen = now
    device.save(update_fields=["last_seen"])

    # Request capabilities on new device or reconnection
    if created or not was_online:
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
            logger.warning("Capabilities response timeout for %s", device_id)

    if not device.is_approved:
        logger.info("Dropping sensor data from unapproved device: %s", device_id)
        return

    # Insert readings
    readings = []
    channel_layer = get_channel_layer()

    for metric, value in data.items():
        if not isinstance(metric, str) or len(metric) > MAX_METRIC_NAME_LEN or not SAFE_IDENTIFIER_RE.match(metric):
            continue
        try:
            float_value = float(value)
        except (ValueError, TypeError):
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


def handle_status_message(device_type: str, device_id: str, payload: bytes):
    """Process a device status (warning/error) message."""
    if len(payload) > MAX_PAYLOAD_SIZE:
        logger.warning("Status payload too large from %s/%s", device_type, device_id)
        return

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Invalid status JSON from %s/%s", device_type, device_id)
        return

    if not isinstance(data, dict):
        logger.warning("Non-dict status from %s/%s", device_type, device_id)
        return

    try:
        device = Device.objects.get(device_id=device_id)
    except Device.DoesNotExist:
        logger.warning("Status from unknown device: %s", device_id)
        return

    level = data.get("level", "")
    if level not in ("", "ok", "warning", "error"):
        logger.warning("Invalid alert level from %s: %s", device_id, level)
        return

    # "ok" clears the alert
    if level in ("", "ok"):
        device.alert_level = ""
        device.alert_message = ""
    else:
        device.alert_level = level
        message = data.get("message", "")
        if isinstance(message, str):
            device.alert_message = message[:256]

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
        logger.warning("Capabilities payload too large from %s/%s", device_type, device_id)
        return

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Invalid capabilities JSON from %s/%s", device_type, device_id)
        return

    if not isinstance(data, dict):
        logger.warning("Non-dict capabilities from %s/%s", device_type, device_id)
        return

    logger.info(
        "Received capabilities from %s/%s: keys=%s has_units=%s has_command_params=%s",
        device_type, device_id, list(data.keys()),
        "units" in data, "command_params" in data,
    )

    try:
        device = Device.objects.get(device_id=device_id)
    except Device.DoesNotExist:
        logger.warning("Capabilities from unknown device: %s", device_id)
        return

    hardware_id = data.get("hardware_id", "")
    if isinstance(hardware_id, str) and len(hardware_id) <= 256:
        device.hardware_id = hardware_id

    publish_interval = data.get("publish_interval", 0)
    if isinstance(publish_interval, (int, float)) and 0 < publish_interval <= 86400:
        device.publish_interval = int(publish_interval)

    capabilities = {}
    if isinstance(data.get("metrics"), list):
        capabilities["metrics"] = [
            m for m in data["metrics"]
            if isinstance(m, str) and SAFE_IDENTIFIER_RE.match(m)
        ]
    if isinstance(data.get("commands"), list):
        capabilities["commands"] = [
            c for c in data["commands"]
            if isinstance(c, str) and SAFE_IDENTIFIER_RE.match(c)
        ]
    if isinstance(data.get("units"), dict):
        capabilities["units"] = {
            k: v for k, v in data["units"].items()
            if isinstance(k, str) and SAFE_IDENTIFIER_RE.match(k)
            and isinstance(v, str) and len(v) <= 16
        }
    valid_param_types = {"number", "string", "boolean"}
    if isinstance(data.get("command_params"), dict):
        command_params = {}
        for cmd_name, params in data["command_params"].items():
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
    logger.info(
        "Stored capabilities for %s: metrics=%s units=%s commands=%s command_params=%s",
        device_id,
        capabilities.get("metrics"),
        capabilities.get("units"),
        capabilities.get("commands"),
        capabilities.get("command_params"),
    )
    device.capabilities_requested_at = None
    if device.alert_level == "error" and device.alert_message == "no_capabilities_response":
        device.alert_level = ""
        device.alert_message = ""
        DeviceStatusLog.objects.create(
            time=dj_timezone.now(), device=device,
            alert_level="", alert_message="",
        )
    device.save(update_fields=[
        "hardware_id", "publish_interval", "capabilities",
        "capabilities_requested_at", "alert_level", "alert_message",
    ])


def handle_ack_message(device_type: str, device_id: str, payload: bytes):
    """Process a command acknowledgement from a device."""
    if len(payload) > MAX_PAYLOAD_SIZE:
        logger.warning("Ack payload too large from %s/%s", device_type, device_id)
        return

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Invalid ack JSON from %s/%s", device_type, device_id)
        return

    if not isinstance(data, dict):
        logger.warning("Non-dict ack from %s/%s", device_type, device_id)
        return

    try:
        device = Device.objects.get(device_id=device_id)
    except Device.DoesNotExist:
        logger.warning("Ack from unknown device: %s", device_id)
        return

    action = data.get("action", "")
    status = data.get("status", "")

    if not action or status not in ("ok", "error"):
        logger.warning("Invalid ack format from %s: action=%s status=%s", device_id, action, status)
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
        logger.info("Acked command %s for device %s (status=%s)", action, device_id, status)
    else:
        logger.warning("No matching unacked command '%s' for device %s", action, device_id)

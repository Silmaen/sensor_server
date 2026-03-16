import json
import logging
import re

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.utils import timezone as dj_timezone

from devices.models import Device
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

    device.is_online = True
    device.last_seen = now
    device.save(update_fields=["is_online", "last_seen"])

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
    """Process a device status (online/offline) message."""
    status = payload.decode("utf-8", errors="replace").strip().lower()
    is_online = status == "online"

    device, created = Device.objects.get_or_create(
        device_id=device_id,
        defaults={"device_type": device_type},
    )
    if created:
        logger.info("Auto-discovered device via status: %s (type=%s)", device_id, device_type)

    device.is_online = is_online
    if is_online:
        device.last_seen = dj_timezone.now()
    device.save(update_fields=["is_online", "last_seen"])

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        "live_readings",
        {
            "type": "device_status",
            "status": {
                "device_id": device_id,
                "is_online": is_online,
                "device_name": device.effective_name,
            },
        },
    )

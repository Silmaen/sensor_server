---
paths:
  - "web/mqtt_bridge/**"
  - "web/devices/**"
  - "mosquitto/**"
---

# MQTT conventions

- Topic pattern: `{device_type}/{device_id}/{message_type}` where message_type is `sensors`, `status`, or `command`.
- device_type and device_id must match `[a-zA-Z0-9_-]+` (validated by SAFE_IDENTIFIER_RE).
- Sensor payloads are JSON dicts with metric names as keys and numeric values.
- Metric names must match `[a-zA-Z0-9_-]+`, max 64 chars.
- Max payload size: 10 KB.
- Commands are published with retain=True.
- MQTT credentials are in `.env` (MQTT_USER / MQTT_PASSWORD), generated at container startup.
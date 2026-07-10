---
paths:
  - "web/mqtt_bridge/**"
  - "web/devices/**"
  - "mosquitto/**"
---

# MQTT conventions

- Topic pattern: `{device_type}/{device_id}/{message_type}`. Server→device: `command`. Device→server: `sensors`, `status`, `capabilities`, `commands`, `calibration`, `ack`.
- device_type and device_id must match `[a-zA-Z0-9_-]+` (validated by SAFE_IDENTIFIER_RE).
- Sensor payloads are JSON dicts with metric names as keys and numeric values.
- Metric names must match `[a-zA-Z0-9_-]+`, max 64 chars.
- Max payload size: 10 KB.
- Commands and requests are published with **retain=False, qos=1**: queued for deep-sleep devices via the persistent session, never left retained (a retained command re-fires on every reconnect). Un-acked commands are re-sent by the wake-up flush. This supersedes the earlier retain=True convention.
- MQTT credentials are in `.env` (MQTT_USER / MQTT_PASSWORD), generated at container startup.
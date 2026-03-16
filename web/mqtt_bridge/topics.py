# MQTT topic patterns
# Sensors: {device_type}/{device_id}/sensors  — JSON payload with metric values
# Status:  {device_type}/{device_id}/status   — "online" / "offline" (LWT)
# Command: {device_type}/{device_id}/command  — JSON command to device (published by server)

TOPIC_SENSORS = "+/+/sensors"
TOPIC_STATUS = "+/+/status"

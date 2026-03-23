# MQTT topic patterns
# Sensors:      {device_type}/{device_id}/sensors       — JSON payload with metric values
# Status:       {device_type}/{device_id}/status        — JSON alert: {"level": "warning|error", "message": "..."}
# Command:      {device_type}/{device_id}/command       — JSON command to device (published by server)
# Capabilities: {device_type}/{device_id}/capabilities  — JSON capabilities response from device

TOPIC_SENSORS = "+/+/sensors"
TOPIC_STATUS = "+/+/status"
TOPIC_CAPABILITIES = "+/+/capabilities"

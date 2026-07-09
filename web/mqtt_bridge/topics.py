# MQTT topic patterns
# Sensors:      {device_type}/{device_id}/sensors       — JSON payload with metric values
# Status:       {device_type}/{device_id}/status        — JSON alert: {"level": "warning|error", "message": "..."}
# Command:      {device_type}/{device_id}/command       — JSON command to device (published by server)
# Capabilities: {device_type}/{device_id}/capabilities  — JSON capabilities response from device (identity + metrics)
# Commands:     {device_type}/{device_id}/commands      — JSON command list + params from device (answer to request_commands)
# Calibration:  {device_type}/{device_id}/calibration   — JSON calibration report from device (answer to request_calibration)
# Ack:          {device_type}/{device_id}/ack           — JSON command acknowledgement from device

TOPIC_SENSORS = "+/+/sensors"
TOPIC_STATUS = "+/+/status"
TOPIC_CAPABILITIES = "+/+/capabilities"
TOPIC_COMMANDS = "+/+/commands"
TOPIC_CALIBRATION = "+/+/calibration"
TOPIC_ACK = "+/+/ack"

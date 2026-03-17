---
name: publish-sensor
description: Publish a fake sensor reading to MQTT for testing
disable-model-invocation: true
allowed-tools: Bash, Read
argument-hint: "[device_type/device_id metric1:value1 metric2:value2]"
---

Publish a fake sensor reading to the MQTT broker for testing.

1. First read `.env` to get MQTT_USER and MQTT_PASSWORD.
2. Parse the arguments: first arg is `device_type/device_id`, remaining args are `metric:value` pairs.
3. If no arguments, use defaults: `thermo/test01 temperature:22.5 humidity:45.2`.
4. Build a JSON payload from the metric:value pairs.
5. Publish via:
```bash
docker compose exec mosquitto mosquitto_pub \
  -h localhost \
  -t "{device_type}/{device_id}/sensors" \
  -m '{json_payload}' \
  -u {MQTT_USER} -P {MQTT_PASSWORD}
```
6. Then verify the reading was inserted:
```bash
docker compose exec web python manage.py shell -c "
from readings.models import SensorReading
for r in SensorReading.objects.order_by('-time')[:5]:
    print(f'{r.device_id}/{r.metric}={r.value} @ {r.time}')
"
```
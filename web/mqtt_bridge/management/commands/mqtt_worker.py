import logging
import signal
import sys
import time

import paho.mqtt.client as mqtt
from django.conf import settings
from django.core.management.base import BaseCommand

from mqtt_bridge.services import (
    handle_capabilities_message,
    handle_sensor_message,
    handle_status_message,
    parse_topic,
)
from mqtt_bridge.topics import TOPIC_CAPABILITIES, TOPIC_SENSORS, TOPIC_STATUS

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "MQTT subscriber worker — bridges MQTT messages to the database and WebSocket layer"

    def handle(self, *args, **options):
        self.stdout.write("Starting MQTT worker...")

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.username_pw_set(settings.MQTT_USER, settings.MQTT_PASSWORD)

        client.on_connect = self._on_connect
        client.on_message = self._on_message

        def shutdown(signum, frame):
            self.stdout.write("Shutting down MQTT worker...")
            client.disconnect()
            sys.exit(0)

        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)

        while True:
            try:
                client.connect(settings.MQTT_HOST, settings.MQTT_PORT)
                client.loop_forever()
            except ConnectionRefusedError:
                logger.warning("MQTT connection refused, retrying in 5s...")
                time.sleep(5)
            except Exception:
                logger.exception("MQTT worker error, retrying in 5s...")
                time.sleep(5)

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            self.stdout.write(self.style.SUCCESS("Connected to MQTT broker"))
            client.subscribe([
                (TOPIC_SENSORS, 1),
                (TOPIC_STATUS, 1),
                (TOPIC_CAPABILITIES, 1),
            ])
        else:
            logger.error("MQTT connect failed: %s", reason_code)

    def _on_message(self, client, userdata, msg):
        parsed = parse_topic(msg.topic)
        if parsed is None:
            return

        device_type, device_id, msg_type = parsed

        try:
            if msg_type == "sensors":
                handle_sensor_message(device_type, device_id, msg.payload)
            elif msg_type == "status":
                handle_status_message(device_type, device_id, msg.payload)
            elif msg_type == "capabilities":
                handle_capabilities_message(device_type, device_id, msg.payload)
        except Exception:
            logger.exception("Error processing %s", msg.topic)

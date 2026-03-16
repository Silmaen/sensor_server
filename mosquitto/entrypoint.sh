#!/bin/sh
set -e

PASSWD_FILE=/mosquitto/data/passwd

# Generate password file from env vars if it doesn't exist yet
if [ ! -f "$PASSWD_FILE" ]; then
    echo "Generating MQTT password file..."
    touch "$PASSWD_FILE"
    mosquitto_passwd -b "$PASSWD_FILE" "$MQTT_USER" "$MQTT_PASSWORD"
    echo "MQTT user '$MQTT_USER' created."
else
    # Update password in case env changed
    mosquitto_passwd -b "$PASSWD_FILE" "$MQTT_USER" "$MQTT_PASSWORD"
fi

# Fix permissions to satisfy mosquitto 2.x
chown mosquitto:mosquitto "$PASSWD_FILE"
chmod 0700 "$PASSWD_FILE"

exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf

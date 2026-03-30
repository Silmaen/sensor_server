# MQTT Communication Protocol

This document describes the MQTT protocol between IoT sensor devices (ESP8266,
MKR WiFi 1010, ESP32) and the server. It covers topic structure, message formats,
the device lifecycle, battery-powered device support, and error handling.

## Overview

All communication happens over MQTT via a shared Mosquitto broker.
The server subscribes to device topics and publishes commands.
Devices publish sensor data, status alerts, and capabilities responses.

```
Device (ESP8266)                           Server (Django MQTT worker)
      |                                            |
      |--- sensors  /{type}/{id}/sensors --------->|  Sensor readings (JSON)
      |--- status   /{type}/{id}/status  --------->|  Alerts: warning/error (JSON)
      |--- capabilities /{type}/{id}/capabilities->|  Capabilities response (JSON)
      |--- ack        /{type}/{id}/ack ----------->|  Command acknowledgement (JSON)
      |                                            |
      |<-- command  /{type}/{id}/command ----------|  Commands (JSON)
      |                                            |
```

## Topic structure

All topics follow the pattern:

```
{device_type}/{device_id}/{message_type}
```

| Segment        | Description                                                   | Constraints                            |
|----------------|---------------------------------------------------------------|----------------------------------------|
| `device_type`  | Category of device (e.g. `thermo`, `relay`)                   | Alphanumeric, `-`, `_`. Max 128 chars. |
| `device_id`    | Unique identifier on the network                              | Alphanumeric, `-`, `_`. Max 128 chars. |
| `message_type` | One of: `sensors`, `status`, `command`, `capabilities`, `ack` |                                        |

Identifiers containing MQTT wildcards (`+`, `#`) or slashes (`/`) are rejected.

## Message types

### 1. Sensor readings (`sensors`)

**Direction:** Device -> Server

**Topic:** `{device_type}/{device_id}/sensors`

**Payload:** JSON object mapping metric names to numeric values.

```json
{
  "temperature": 22.5,
  "humidity": 45.2
}
```

**Constraints:**
- Maximum payload size: 10 KB
- Metric names: alphanumeric, `-`, `_`. Max 64 chars.
- Values: must be valid numbers (parsed as `float`)
- Unknown metrics are silently ignored

**Server behavior:**
- Auto-discovers the device if it does not exist (creates it as unapproved)
- Updates `last_seen` timestamp
- **Drops the readings** if the device is not approved by an admin
- Stores readings in TimescaleDB and pushes them to WebSocket clients

### 2. Status alerts (`status`)

**Direction:** Device -> Server

**Topic:** `{device_type}/{device_id}/status`

**Payload:** JSON object with an alert level and optional message.

```json
{"level": "warning", "message": "low_battery"}
```

| Field     | Type   | Required | Description                           |
|-----------|--------|:--------:|---------------------------------------|
| `level`   | string |   Yes    | `"warning"`, `"error"`, or `"ok"`     |
| `message` | string |    No    | Human-readable detail (max 256 chars) |

- `"ok"` (or empty `level`) **clears** any existing alert on the device.
- `"warning"` indicates a degraded state (e.g. low battery, sensor drift).
- `"error"` indicates a failure (e.g. sensor hardware fault).

**Important:** Devices do **not** publish online/offline status. Online detection
is computed server-side (see [Online/offline detection](#onlineoffline-detection)).

**Server behavior:**
- Only accepts messages from already-known devices (no auto-discovery on status)
- Stores `alert_level` and `alert_message` on the device record
- Broadcasts the alert to WebSocket clients in real time

### 3. Commands (`command`)

**Direction:** Server -> Device

**Topic:** `{device_type}/{device_id}/command`

**Payload:** JSON object with an `action` field and optional parameters.

```json
{"action": "set_interval", "value": 30}
```

The server publishes commands in three situations:
- **User-initiated:** an admin or resident sends a command from the web UI
- **Automatic:** the server sends `request_capabilities` (see below)
- **Wake-up flush:** when a device reconnects, the server re-publishes all
  unacknowledged commands (see [Battery-powered devices](#battery-powered-devices-deep-sleep))

After every user-initiated command, the server automatically sends a follow-up
`request_capabilities` to refresh the device's reported capabilities,
since a command may change them (e.g. `set_interval` changes the publish rate).

**Retain flag:** User-initiated commands are published with `retain=True` so the
device receives them when it reconnects. `request_capabilities` uses `retain=False`.

### 4. Capabilities (`capabilities`)

**Direction:** Device -> Server

**Topic:** `{device_type}/{device_id}/capabilities`

**Payload:** JSON object describing the device's identity and capabilities.

```json
{
  "id": "ESP-ABCDEF123456",
  "intrvl": 60,
  "metrics": {"temperature": "°C", "humidity": "%", "pressure": "hPa"},
  "cmds": {
    "set_interval": [{"n": "value", "t": "number"}],
    "set_led": [{"n": "state", "t": "boolean"}, {"n": "brightness", "t": "number"}]
  }
}
```

| Field     | Type   | Required | Description                                                          |
|-----------|--------|:--------:|----------------------------------------------------------------------|
| `id`      | string |   Yes    | Unique hardware identifier (e.g. ESP chip ID). Max 256 chars.        |
| `intrvl`  | number |   Yes    | Sensor publish frequency in seconds (1-86400).                       |
| `metrics` | object |   Yes    | Metric name → unit string (`""` if no unit). Max 16 chars per unit.  |
| `cmds`    | object |   Yes    | Command name → array of parameter definitions (`[]` if no params).   |

**Parameter definition format:**

Each entry in a `cmds` params array is an object with:
- `n` (string, required): The parameter name.
- `t` (string, required): One of `"number"`, `"string"`, `"boolean"`.

**Notes:**
- `cmds` should **not** include `request_capabilities` itself — this command
  is always implicitly supported by all devices.
- Metric and command names follow the same identifier rules (alphanumeric, `-`, `_`).
- The server extracts metric names from `metrics` keys and units from their values.
  Empty unit strings are ignored.

### 5. Command acknowledgement (`ack`)

**Direction:** Device -> Server

**Topic:** `{device_type}/{device_id}/ack`

**Payload:** JSON object acknowledging a previously received command.

```json
{"action": "set_interval", "status": "ok"}
```

| Field    | Type   | Required | Description                                      |
|----------|--------|:--------:|--------------------------------------------------|
| `action` | string |   Yes    | The command action being acknowledged.           |
| `status` | string |   Yes    | `"ok"` (command executed) or `"error"` (failed). |

**Server behavior:**
- Finds the most recent unacknowledged `CommandLog` entry matching the device and action.
- Sets `acked = True` and records `acked_at` timestamp.
- If no matching unacknowledged command is found, the message is logged and ignored.

**Notes:**
- Devices should send an ack as soon as the command has been processed.
- The `action` field must exactly match the `action` in the original command.
- Acks are optional — the server does not require them, but they enable
  the admin UI to show whether a command was executed.

## Capabilities request flow

The server requests capabilities by publishing on the device's `command` topic:

```json
{"action": "request_capabilities"}
```

**This is sent automatically in three cases:**
1. **New device discovered** — first message received from an unknown `device_id`
2. **Device reconnection** — device was offline and starts publishing again
3. **After every command** — to refresh capabilities that may have changed

Admins can also trigger it manually from the device detail page in the web UI.

### Expected response

The device must respond on its `capabilities` topic within **60 seconds**.

### Timeout handling

If the device publishes sensor data but has not responded to a pending
capabilities request within the timeout:
- The device is flagged with `alert_level = "error"` and
  `alert_message = "no_capabilities_response"`
- This error is visible in the web UI and broadcast via WebSocket
- The error is **automatically cleared** if the device later sends a valid
  capabilities response

```
Server                                      Device
  |                                            |
  |-- {"action": "request_capabilities"} ----->|
  |                                            |
  |    (device has 60s to respond)             |
  |                                            |
  |<--- capabilities response (JSON) ----------|  OK: alert cleared, data stored
  |                                            |
  |    --- OR, if no response: ---             |
  |                                            |
  |    (next sensor message arrives)           |
  |    -> timeout detected                     |
  |    -> alert_level = "error"                |
  |    -> alert_message = "no_capabilities_response"
  |                                            |
```

## Online/offline detection

Devices do **not** send explicit online/offline messages. The server computes
online status from the last time data was received:

```
is_online = (now - last_seen) < 3 * publish_interval
```

| Condition                         | Timeout used                 |
|-----------------------------------|------------------------------|
| `publish_interval` is known (>0)  | 3 &times; `publish_interval` |
| `publish_interval` is unknown (0) | 5 minutes (300 seconds)      |

`last_seen` is updated whenever the server receives a `sensors` or `status`
message from the device.

## Device lifecycle

```
                  +--------------+
                  |  Unknown     |
                  +--------------+
                        |
                        | First MQTT message (sensors or status)
                        v
                  +--------------+
                  |  Discovered  |  (auto-created, is_approved=False)
                  |  (pending)   |  -> capabilities requested automatically
                  +--------------+
                        |
                        | Admin approves from web UI
                        v
                  +--------------+
                  |  Approved    |  Sensor data is stored and displayed
                  |  (active)    |  Commands can be sent
                  +--------------+
                       / \
                      /   \
        No data for  /     \  Admin revokes
        3x interval /       \
                   v         v
            +-----------+  +-----------+
            |  Offline  |  |  Revoked  |  (data dropped again)
            +-----------+  +-----------+
                  |
                  | Sensor data received again
                  v
            +--------------+
            |  Reconnected |  -> pending commands flushed,
            |  (active)    |     then capabilities re-requested
            +--------------+
```

## Battery-powered devices (deep sleep)

Battery-powered devices (ESP8266/ESP32 in deep sleep, MKR WiFi 1010 on battery)
disconnect from WiFi and MQTT between sensor readings to save power. They cannot
receive commands while asleep. The server handles this transparently through a
**command flush on wake-up** mechanism.

### The problem

- MQTT `retain` only keeps the **last** message per topic. If multiple commands
  are sent while the device sleeps, only the last one would be delivered.
- MQTT persistent sessions (`clean_session=false`) are unreliable over long
  sleep periods (brokers may expire the session).
- The device has no connectivity during sleep — it cannot subscribe or receive
  anything.

### How it works (server side)

When the server detects that a device has come back online (first `sensors`
message after being offline), it automatically:

1. **Flushes all pending commands** — re-publishes every unacknowledged
   `CommandLog` entry, in chronological order (oldest first), with `retain=True`.
2. **Requests capabilities** — sends `{"action": "request_capabilities"}` as
   usual, so the device reports its current state after processing the commands.

This means the device does not need any special protocol support. It simply
needs to **stay connected long enough** after publishing its sensor data to
receive and process the queued commands.

### Firmware implementation guide

The wake-up cycle for a battery-powered device should follow this sequence:

```
┌─────────────────────────────────────────────────────────────┐
│  1. WAKE UP (timer / deep sleep reset)                      │
│                                                             │
│  2. CONNECT WiFi + MQTT                                     │
│                                                             │
│  3. SUBSCRIBE to {type}/{id}/command                        │
│     (must happen BEFORE publishing sensors, so the device   │
│      is ready to receive commands as soon as the server     │
│      detects the wake-up)                                   │
│                                                             │
│  4. PUBLISH sensor readings on {type}/{id}/sensors          │
│     → This triggers the server-side command flush.          │
│                                                             │
│  5. WAIT for incoming commands (3-5 seconds)                │
│     Call mqttClient.loop() / client.check_msg() in a loop.  │
│     Process each command as it arrives:                      │
│       a. Execute the action (set_interval, set_led, etc.)   │
│       b. Send ack on {type}/{id}/ack                        │
│     If a "request_capabilities" is received:                │
│       → Prepare capabilities response (do NOT ack)          │
│                                                             │
│  6. PUBLISH capabilities on {type}/{id}/capabilities        │
│     (always send at the end — reflects state after          │
│      all commands have been applied)                        │
│                                                             │
│  7. DISCONNECT MQTT + WiFi                                  │
│                                                             │
│  8. DEEP SLEEP for publish_interval seconds                 │
└─────────────────────────────────────────────────────────────┘
```

### Key points for firmware developers

- **Subscribe before publishing.** The server sends pending commands as soon as
  it receives a `sensors` message. If the device subscribes after publishing,
  commands may arrive before the subscription is active and be lost.

- **Wait at least 3 seconds after publishing sensors.** The server needs time
  to process the message, query pending commands, and publish them. 5 seconds
  is a safe margin for slow networks.

- **Process commands in order.** The server sends them chronologically. Each
  command should be fully executed and acked before processing the next one.

- **Do NOT ack `request_capabilities`.** This is not a regular command. The
  server knows it has been fulfilled when it receives the `capabilities`
  response. Simply publish your capabilities at the end of the wake cycle.

- **Always send capabilities last.** This ensures the reported state (e.g.
  `publish_interval`) reflects all commands that were just applied.

- **Use `clean_session=true`.** Since the device sleeps for long periods,
  persistent sessions are unreliable. The server-side flush makes them
  unnecessary.

- **Multiple commands may arrive.** If several commands were queued while the
  device was asleep, they all arrive in the wait window. The firmware must
  handle receiving more than one command per wake cycle.

### Arduino pseudocode (ESP8266 / ESP32 / MKR WiFi 1010)

```cpp
#include <PubSubClient.h>

const char* CMD_TOPIC  = "thermo/living01/command";
const char* SENS_TOPIC = "thermo/living01/sensors";
const char* ACK_TOPIC  = "thermo/living01/ack";
const char* CAP_TOPIC  = "thermo/living01/capabilities";

bool capabilitiesRequested = false;

void onMessage(char* topic, byte* payload, unsigned int length) {
    // Parse JSON command
    StaticJsonDocument<256> doc;
    deserializeJson(doc, payload, length);
    const char* action = doc["action"];

    if (strcmp(action, "request_capabilities") == 0) {
        // Do NOT ack — just flag it, capabilities sent at end of cycle
        capabilitiesRequested = true;
        return;
    }

    // Execute command
    if (strcmp(action, "set_interval") == 0) {
        sleepSeconds = doc["value"];
    }
    // ... handle other commands ...

    // Ack the command
    StaticJsonDocument<128> ack;
    ack["action"] = action;
    ack["status"] = "ok";
    char ackBuf[128];
    serializeJson(ack, ackBuf);
    mqttClient.publish(ACK_TOPIC, ackBuf);
}

void loop() {
    // 1. Connect
    connectWiFi();
    mqttClient.setCallback(onMessage);
    mqttClient.connect(clientId, mqttUser, mqttPass,
                        /* cleanSession */ true);

    // 2. Subscribe BEFORE publishing
    mqttClient.subscribe(CMD_TOPIC);

    // 3. Publish sensor data (triggers server-side command flush)
    char sensorPayload[128];
    snprintf(sensorPayload, sizeof(sensorPayload),
             "{\"temperature\":%.1f,\"humidity\":%.1f}", temp, hum);
    mqttClient.publish(SENS_TOPIC, sensorPayload);

    // 4. Wait for commands (5 seconds)
    capabilitiesRequested = false;
    unsigned long start = millis();
    while (millis() - start < 5000) {
        mqttClient.loop();  // Processes incoming messages via callback
        delay(50);
    }

    // 5. Always send capabilities (reflects post-command state)
    publishCapabilities();

    // 6. Disconnect and sleep
    mqttClient.disconnect();
    WiFi.disconnect();

#if defined(ESP8266)
    ESP.deepSleep(sleepSeconds * 1e6);
#elif defined(ESP32)
    esp_deep_sleep(sleepSeconds * 1e6);
#else
    // MKR WiFi 1010: use RTCZero or LowPower library
    LowPower.deepSleep(sleepSeconds * 1000);
#endif
}
```

### Timing considerations

| Parameter          | Recommended value | Notes                                        |
|--------------------|-------------------|----------------------------------------------|
| Wait window        | 5 seconds         | Time to receive commands after publishing     |
| `publish_interval` | 60-300 seconds    | Balance between freshness and battery life    |
| Offline threshold  | 3 &times; interval | Server considers device offline after this   |

With a 5-minute interval and a 5-second wake window, the device is awake ~1.7%
of the time. Actual battery impact depends on WiFi connection time (typically
1-3 seconds) and transmit power.

### Example session (battery-powered device)

```
# 1. Device wakes up, connects, subscribes to command topic

# 2. Device publishes sensor data
thermo/battery01/sensors  <- {"temperature": 19.3, "humidity": 62}

# 3. Server detects reconnection, flushes 2 pending commands
thermo/battery01/command  -> {"action": "set_interval", "value": 120}
thermo/battery01/command  -> {"action": "set_led", "state": false}

# 4. Server requests capabilities
thermo/battery01/command  -> {"action": "request_capabilities"}

# 5. Device processes set_interval, sends ack
thermo/battery01/ack      <- {"action": "set_interval", "status": "ok"}

# 6. Device processes set_led, sends ack
thermo/battery01/ack      <- {"action": "set_led", "status": "ok"}

# 7. Device sends capabilities (reflects new interval + led state)
thermo/battery01/capabilities <- {
    "id": "MKR-1010-ABC123",
    "intrvl": 120,
    "metrics": {"temperature": "°C", "humidity": "%"},
    "cmds": {
        "set_interval": [{"n": "value", "t": "number"}],
        "set_led": [{"n": "state", "t": "boolean"}]
    }
}

# 8. Device disconnects and enters deep sleep for 120 seconds

# 9. Server waits 360s (3 × 120s) with no data -> device marked offline
#    (this is normal for battery devices — the device is just sleeping)
```

## Authentication

All MQTT clients (devices and server) authenticate with the same shared
credentials (`MQTT_USER` / `MQTT_PASSWORD` from `.env`). The password file
is auto-generated by the Mosquitto entrypoint at startup.

Anonymous access is disabled (`allow_anonymous false`).

**Note:** There are currently no per-device MQTT ACLs. Device identity is
verified at the application level through the capabilities/approval workflow.

## Payload validation summary

| Check                | Applies to     | Rule                                           | On failure      |
|----------------------|----------------|------------------------------------------------|-----------------|
| Payload size         | All            | Max 10 KB                                      | Message dropped |
| JSON validity        | All            | Must be valid JSON object                      | Message dropped |
| Topic identifiers    | All            | `^[a-zA-Z0-9_\-]+$`, max 128 chars             | Message dropped |
| Metric names         | `sensors`      | `^[a-zA-Z0-9_\-]+$`, max 64 chars              | Metric skipped  |
| Metric values        | `sensors`      | Must parse as float                            | Metric skipped  |
| Alert level          | `status`       | Must be `""`, `"ok"`, `"warning"`, `"error"`   | Message dropped |
| Hardware ID          | `capabilities` | String, max 256 chars                          | Field ignored   |
| Publish interval     | `capabilities` | Number, 1-86400                                | Field ignored   |
| Command/metric names | `capabilities` | `^[a-zA-Z0-9_\-]+$`                            | Entry skipped   |
| Unit strings         | `capabilities` | String, max 16 chars                           | Field ignored   |
| Param type           | `capabilities` | Must be `"number"`, `"string"`, or `"boolean"` | Entry skipped   |
| Ack action           | `ack`          | Non-empty string                               | Message dropped |
| Ack status           | `ack`          | Must be `"ok"` or `"error"`                    | Message dropped |

## Example session

```
# 1. Device powers on and publishes its first sensor reading
thermo/living01/sensors  <- {"temperature": 22.5, "humidity": 45}

# 2. Server auto-discovers device, requests capabilities
thermo/living01/command  -> {"action": "request_capabilities"}

# 3. Device responds with capabilities
thermo/living01/capabilities <- {
    "id": "ESP-A1B2C3D4E5F6",
    "intrvl": 60,
    "metrics": {"temperature": "°C", "humidity": "%"},
    "cmds": {"set_interval": [{"n": "value", "t": "number"}]}
}

# 4. Admin approves the device from the web UI
#    From now on, sensor data is stored

# 5. Device reports low battery
thermo/living01/status   <- {"level": "warning", "message": "low_battery"}

# 6. Admin changes the publish interval
thermo/living01/command  -> {"action": "set_interval", "value": 120}

# 7. Server automatically re-requests capabilities
thermo/living01/command  -> {"action": "request_capabilities"}

# 8. Device responds with updated capabilities
thermo/living01/capabilities <- {
    "id": "ESP-A1B2C3D4E5F6",
    "intrvl": 120,
    "metrics": {"temperature": "°C", "humidity": "%"},
    "cmds": {"set_interval": [{"n": "value", "t": "number"}]}
}

# 9. No data for 360 seconds (3 x 120s) -> server considers device offline

# 10. Device publishes again -> reconnection detected
thermo/living01/sensors  <- {"temperature": 21.8, "humidity": 48}

# 11. Server flushes any pending unacked commands, then re-requests capabilities
thermo/living01/command  -> {"action": "request_capabilities"}
```

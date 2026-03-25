# MQTT Communication Protocol

This document describes the MQTT protocol between IoT sensor devices (ESP8266)
and the server. It covers topic structure, message formats, the device lifecycle,
and error handling.

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
      |                                            |
      |<-- command  /{type}/{id}/command ----------|  Commands (JSON)
      |                                            |
```

## Topic structure

All topics follow the pattern:

```
{device_type}/{device_id}/{message_type}
```

| Segment        | Description                                            | Constraints                                      |
|----------------|--------------------------------------------------------|--------------------------------------------------|
| `device_type`  | Category of device (e.g. `thermo`, `relay`)            | Alphanumeric, `-`, `_`. Max 128 chars.           |
| `device_id`    | Unique identifier on the network                       | Alphanumeric, `-`, `_`. Max 128 chars.           |
| `message_type` | One of: `sensors`, `status`, `command`, `capabilities` |                                                  |

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

The server publishes commands in two situations:
- **User-initiated:** an admin or resident sends a command from the web UI
- **Automatic:** the server sends `request_capabilities` (see below)

After every command, the server automatically sends a follow-up
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
  "hardware_id": "ESP-ABCDEF123456",
  "publish_interval": 60,
  "metrics": ["temperature", "humidity", "pressure"],
  "units": {
    "temperature": "°C",
    "humidity": "%",
    "pressure": "hPa"
  },
  "commands": ["set_interval", "set_led"],
  "command_params": {
    "set_interval": [{"name": "value", "type": "number"}],
    "set_led": [
      {"name": "state", "type": "boolean"},
      {"name": "brightness", "type": "number"}
    ]
  }
}
```

| Field              | Type     | Required | Description                                                                    |
|--------------------|----------|:--------:|--------------------------------------------------------------------------------|
| `hardware_id`      | string   |   Yes    | Unique hardware identifier (e.g. ESP chip ID). Max 256 chars.                  |
| `publish_interval` | number   |   Yes    | Sensor publish frequency in seconds (1-86400).                                 |
| `metrics`          | string[] |   Yes    | List of metric names the device reports.                                       |
| `units`            | object   |    No    | Mapping of metric name to unit string (e.g. `"°C"`, `"%"`). Max 16 chars/unit. |
| `commands`         | string[] |   Yes    | List of accepted command actions.                                              |
| `command_params`   | object   |    No    | Mapping of command name to array of parameter definitions (see below).         |

**Parameter definition format:**

Each entry in a `command_params` array is an object with:
- `name` (string, required): The parameter name.
- `type` (string, required): One of `"number"`, `"string"`, `"boolean"`.

**Notes:**
- `commands` should **not** include `request_capabilities` itself — this command
  is always implicitly supported by all devices.
- Metric and command names follow the same identifier rules (alphanumeric, `-`, `_`).
- `units` and `command_params` are optional. If omitted, the server displays metrics
  without units and provides a raw JSON command input.

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
            |  Reconnected |  -> capabilities re-requested automatically
            |  (active)    |
            +--------------+
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

## Example session

```
# 1. Device powers on and publishes its first sensor reading
thermo/living01/sensors  <- {"temperature": 22.5, "humidity": 45}

# 2. Server auto-discovers device, requests capabilities
thermo/living01/command  -> {"action": "request_capabilities"}

# 3. Device responds with capabilities
thermo/living01/capabilities <- {
    "hardware_id": "ESP-A1B2C3D4E5F6",
    "publish_interval": 60,
    "metrics": ["temperature", "humidity"],
    "units": {"temperature": "°C", "humidity": "%"},
    "commands": ["set_interval"],
    "command_params": {
        "set_interval": [{"name": "value", "type": "number"}]
    }
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
    "hardware_id": "ESP-A1B2C3D4E5F6",
    "publish_interval": 120,
    "metrics": ["temperature", "humidity"],
    "units": {"temperature": "°C", "humidity": "%"},
    "commands": ["set_interval"],
    "command_params": {
        "set_interval": [{"name": "value", "type": "number"}]
    }
}

# 9. No data for 360 seconds (3 x 120s) -> server considers device offline

# 10. Device publishes again -> reconnection detected
thermo/living01/sensors  <- {"temperature": 21.8, "humidity": 48}

# 11. Server re-requests capabilities (reconnection)
thermo/living01/command  -> {"action": "request_capabilities"}
```

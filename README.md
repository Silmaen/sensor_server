# IoT Sensor Server

A self-hosted IoT sensor and actuator management platform for home automation.
Receives data from ESP8266 devices over MQTT, stores time-series readings in TimescaleDB,
and provides a real-time web dashboard with device control capabilities.

## Stack

| Service       | Image / Tech                      | Role                                 |
|---------------|-----------------------------------|--------------------------------------|
| `mosquitto`   | eclipse-mosquitto:2               | MQTT broker (device communication)   |
| `timescaledb` | timescale/timescaledb:latest-pg16 | Time-series + relational database    |
| `redis`       | redis:7-alpine                    | Django Channels layer + cache        |
| `web`         | Python 3.12 / Django / Daphne     | Web app, API, WebSocket, MQTT bridge |

## Features

- **Auto-discovery with approval**: devices are automatically registered when they first publish to MQTT, but remain in a **pending** state until an admin approves them. Sensor data from unapproved devices is dropped.
- **Capabilities discovery**: the server can send a `request_capabilities` command to any device; the device responds with its hardware ID, supported metrics, and accepted commands.
- **Real-time dashboard**: live sensor readings via WebSocket + HTMX
- **Charts**: historical data visualization with ECharts
- **Device control**: send commands to actuators via MQTT (role-restricted)
- **Role-based access**: Guest / Resident / Admin with approval workflow
- **SSO**: optional Authentik integration via OpenID Connect (falls back to local login)
- **i18n**: English (default) and French, switchable from the navbar

## Quick start

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env — at minimum set DJANGO_SECRET_KEY and passwords

# 2. Start all services
docker compose up --build -d

# 3. Access the web interface
open http://localhost:8000
# Log in with the superuser credentials from .env
```

## MQTT protocol

Devices communicate using the pattern `{device_type}/{device_id}/{message_type}`:

| Topic pattern                  | Direction        | Payload                                               |
|--------------------------------|------------------|-------------------------------------------------------|
| `thermo/living01/sensors`      | Device -> Server | `{"temperature": 22.5, "humidity": 45}`               |
| `thermo/living01/status`       | Device -> Server | `{"level": "warning", "message": "low_battery"}`      |
| `thermo/living01/command`      | Server -> Device | `{"action": "set_interval", "value": 30}`             |
| `thermo/living01/capabilities` | Device -> Server | `{"hardware_id": "...", "publish_interval": 60, ...}` |

For the full protocol specification (message formats, device lifecycle, capabilities
handshake, timeout handling, validation rules), see **[docs/mqtt-protocol.md](docs/mqtt-protocol.md)**.

## Testing with a simulated sensor

```bash
# Publish a fake reading (use credentials from .env)
docker compose exec mosquitto mosquitto_pub \
  -h localhost \
  -t thermo/test01/sensors \
  -m '{"temperature": 22.5, "humidity": 45.2}' \
  -u <MQTT_USER> -P <MQTT_PASSWORD>
```

## User roles

| Role         |      View sensors       | View actuators |  Send commands   | Manage devices |
|--------------|:-----------------------:|:--------------:|:----------------:|:--------------:|
| *(pending)*  |            -            |       -        |        -         |       -        |
| **Guest**    | Limited (temp/humidity) |       No       |        No        |       No       |
| **Resident** |           All           |      All       | Yes (predefined) |       No       |
| **Admin**    |           All           |      All       |       All        |      Yes       |

New users who log in via OIDC start with no role (pending).
An admin must approve them from the Users page.

## Device approval workflow

1. A device publishes to MQTT and is **auto-discovered** (created in the database).
2. The server **automatically requests capabilities** from the device (hardware ID, metrics, commands, publish interval).
3. The device appears in the **"Pending approval"** section of the Devices page with its reported identity.
4. The admin reviews the hardware ID and **approves** the device. Only then are its sensor readings stored and displayed.
5. An approved device can be **revoked** at any time, which stops data ingestion immediately.
6. When a device **reconnects** after being offline, capabilities are automatically re-requested.

## Authentication

- **With Authentik**: set `OIDC_RP_CLIENT_ID` and related variables in `.env`. Users are redirected to Authentik for login.
- **Without Authentik**: leave `OIDC_RP_CLIENT_ID` empty. A local login form is used instead. The superuser is created automatically from `DJANGO_SUPERUSER_*` env vars.

## Environment variables

See [`.env.example`](.env.example) for the full list with descriptions.

Key variables:

| Variable                      | Description                                    | Required |
|-------------------------------|------------------------------------------------|:--------:|
| `DATA_DIR`                    | Host path for persistent data (DB, MQTT, logs) |   Yes    |
| `DJANGO_SECRET_KEY`           | Django secret key (required in production)     |   Yes    |
| `POSTGRES_PASSWORD`           | TimescaleDB password                           |   Yes    |
| `MQTT_USER` / `MQTT_PASSWORD` | MQTT broker credentials (auto-provisioned)     |   Yes    |
| `WEB_EXPOSED_PORT`            | Host port for web UI (default: 8000)           |    No    |
| `MQTT_EXPOSED_PORT`           | Host port for MQTT (default: 1883)             |    No    |
| `OIDC_RP_CLIENT_ID`           | Authentik OIDC client ID (empty = local login) |    No    |

## Project structure

```
sensor_server/
  docker-compose.yml
  .env / .env.example
  docs/                   # Protocol and architecture documentation
  mosquitto/              # Mosquitto config + entrypoint
  web/
    manage.py
    sensor_server/        # Django project (settings, urls, asgi)
    accounts/             # Auth, roles, OIDC backend, approval workflow
    devices/              # Device registry, approval, capabilities, commands
    readings/             # Sensor data, dashboard, charts, WebSocket
    mqtt_bridge/          # MQTT subscriber worker, auto-discovery, capabilities
    templates/            # Base layout
    locale/               # i18n translations (en, fr)
    static/               # Static assets
```

## Persistent data

All persistent data is stored under `DATA_DIR`:

| Path                       | Content                             |
|----------------------------|-------------------------------------|
| `${DATA_DIR}/timescaledb/` | PostgreSQL data                     |
| `${DATA_DIR}/mosquitto/`   | MQTT persistence + generated passwd |
| `${DATA_DIR}/redis/`       | Redis dump                          |
| `${DATA_DIR}/logs/`        | Django application logs             |

## Management commands

```bash
# Run inside the web container
docker compose exec web python manage.py <command>

# Available custom commands:
#   ensure_superuser  — Create superuser from env vars (idempotent)
#   mqtt_worker       — MQTT subscriber (runs automatically via supervisord)
```

## Internationalization

The UI defaults to English. Users can switch to French via the language toggle in the navbar.

To update translations after changing source strings:

```bash
docker compose exec web python manage.py makemessages -l fr
# Edit web/locale/fr/LC_MESSAGES/django.po
docker compose exec web python manage.py compilemessages
```

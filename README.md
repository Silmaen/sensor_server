# IoT Sensor Server

A self-hosted IoT sensor and actuator management platform for home automation.
Receives data from ESP8266 devices over MQTT, stores time-series readings in TimescaleDB,
and provides a real-time web dashboard with device control capabilities.

## Stack

| Service       | Image / Tech                       | Role                                  |
|---------------|------------------------------------|---------------------------------------|
| `mosquitto`   | eclipse-mosquitto:2.1.2-alpine     | MQTT broker (device communication)    |
| `timescaledb` | timescale/timescaledb:2.29.1-pg16  | Time-series + relational database     |
| `redis`       | redis:8.10.0-alpine                | Django Channels layer + cache         |
| `web`         | Python 3.13 / Django 5.2 / Daphne  | Web app, API, WebSocket, MQTT bridge  |
| `nginx`       | nginx:1.30.4-alpine                | Serves static + media (`/fw/`), proxy |

Tags are pinned to an exact version — see [Updating](#updating).

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
    api/                  # Read-only HTTP API (/api/v1/) for external services
    ota/                  # Hardware registry, firmware catalog, publication API, OTA push
    catalog/              # Designed-sensor documentation (Markdown), linked to hardware
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

## Deploying and updating

`./deploy.sh` is the single entry point (it replaces the former `update.sh`): it pulls
the repository and the images, rebuilds, starts, waits for every service to report
healthy, and updates the TimescaleDB extension if the image moved. Migrations,
`collectstatic` and `compilemessages` are left to `web/entrypoint.sh`, which runs them
when the container starts.

```bash
./deploy.sh                    # full deployment
./deploy.sh --no-pull          # deploy the files on disk, fetch nothing
./deploy.sh --dry-run          # print what would run
./deploy.sh check              # is a commit pending? exit 0 = no, 10 = yes, 1 = cannot tell
./deploy.sh status | logs [svc] | stop | restart
./deploy.sh timescale-update   # the extension step, alone
./deploy.sh help               # the full list
```

The name and the mode are part of a contract, not a preference: the homelab console's
per-stack deploy button publishes `deploy <machine> <project>`, and the machine looks
the script up itself — `home-server-stacks` `homelab-probe` accepts `deploy.sh` (first
choice), `deploy` or `update.sh`, next to the compose file, **executable and
git-tracked**, and the wake agent runs it with no arguments, stdin on `/dev/null`, as
the owner of the checkout. So `./deploy.sh` with no argument must be the whole
deployment, and it must never ask a question. `chmod +x` and a commit are what make the
button appear.

Image tags are pinned to an **exact version** in `docker-compose.yml`, so a version
change is a reviewable diff rather than a side effect of a pull.

Every service also carries [wud](https://github.com/getwud/wud) labels, because the
homelab watches container versions centrally (`selene/monitoring` in
`home-server-stacks`) and reports what is behind. Left unlabelled, wud takes the
*greatest* tag of a repository, which turns that report into noise — a beta, a foreign
variant, or a 401 on an image that exists in no registry:

| Service       | `wud` label                              | Reported                | Out of scope, on purpose                            |
|---------------|------------------------------------------|-------------------------|-----------------------------------------------------|
| `mosquitto`   | `wud.tag.include: ^2\.1\.\d+-alpine$`    | 2.1 patches             | 2.2/3.0 — 3.0 removes `password_file` (see below)   |
| `timescaledb` | `wud.tag.include: ^\d+\.\d+\.\d+-pg16$`  | TimescaleDB releases    | `-pg17`/`-pg18` — a `pg_upgrade`, not a pull; `-oss` |
| `redis`       | `wud.tag.include: ^8\.\d+\.\d+-alpine$`  | 8.x releases            | Redis 9, `32bit-stretch`, `-alpine3.x` duplicates    |
| `nginx`       | `wud.tag.include: ^1\.30\.\d+-alpine$`   | stable-branch patches   | mainline (odd minors, always "greater")              |
| `web`         | `wud.watch: 'false'`                     | nothing                 | built here, so absent from every registry            |

Two things to know when editing those labels:

- **Write `$$`, not `$`** — compose interpolates `$`, and a single one silently
  corrupts the regex.
- **Bumping a pin means bumping its regex** on the same line of thought. That is the
  point: the versions the regex excludes are the ones that deserve release notes
  before a deployment.

Mosquitto 2.1 deprecated `password_file` (still used by `mosquitto/mosquitto.conf`) in
favour of the `password-file` plugin, and announced its removal for 3.0. The plugin path
is not usable in the official image yet, so the deprecated option stays — and the wud
regex stops before the major that would break it.

### After a TimescaleDB image bump

The image ships the extension, but the database keeps the version it was created with,
so a new image needs one manual step. It must be the **first statement of a fresh
session**:

```bash
docker compose exec timescaledb \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c 'ALTER EXTENSION timescaledb UPDATE;'

# verify: installed version should now match the image
docker compose exec timescaledb psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -Atc "select extversion from pg_extension where extname='timescaledb'"
```

Skipping it is not fatal — the database keeps working on the older extension — but the
gap widens silently, and continuous aggregates and compression policies are exactly the
features whose fixes live in those releases.

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

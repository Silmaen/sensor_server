# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Project

IoT sensor server for home automation. Receives data from ESP8266 devices over MQTT,
stores time-series in TimescaleDB, serves a real-time dashboard with device control.

## Stack

- **Docker Compose**: mosquitto, timescaledb (pg16), redis, web (Django 5.x / Daphne)
- **Django apps**: `accounts` (auth/roles/OIDC), `devices` (registry/commands), `readings` (time-series/dashboard/WebSocket), `mqtt_bridge` (MQTT worker/auto-discovery), `api` (read-only HTTP API for external services), `ota` (hardware registry, firmware catalog, publication API, OTA push), `catalog` (designed-sensor documentation in Markdown, linked to hardware codes)
- **Auth**: optional Authentik SSO via mozilla-django-oidc; local login fallback when `OIDC_RP_CLIENT_ID` is empty
- **Real-time**: Django Channels WebSocket + HTMX ws extension
- **Charts**: ECharts via CDN; Mermaid via CDN on the catalog detail page only (renders ```mermaid``` diagrams from sensor docs)
- **CSS**: Pico CSS via CDN — no build step
- **i18n**: English (default) + French, `{% trans %}` / `gettext_lazy`, locale files in `web/locale/`
- **Reverse proxy / TLS**: handled by an **external** reverse proxy (not in this repo); the app runs HTTP only. nginx may be added to the stack to serve Django **static + media** from disk (media includes OTA firmware `.bin`, exposed to devices at `/fw/`) — but never TLS. See `.claude/rules/architecture.md`.

## Commands

```bash
# Start everything
docker compose up --build

# Simulate a sensor (use credentials from .env)
docker compose exec mosquitto mosquitto_pub \
  -h localhost -t thermo/test01/sensors \
  -m '{"temperature":22.5,"humidity":45}' \
  -u $MQTT_USER -P $MQTT_PASSWORD

# Django management (inside container)
docker compose exec web python manage.py shell
docker compose exec web python manage.py makemigrations
docker compose exec web python manage.py migrate
docker compose exec web python manage.py makemessages -l fr
docker compose exec web python manage.py compilemessages

# Health check
curl http://localhost:8000/healthz/

# Database backup
./scripts/backup.sh
```

## Key conventions

- **Language**: all code, comments, docstrings, and commit messages in English
- **i18n**: all user-facing strings use `{% trans %}` in templates, `gettext_lazy` / `gettext` in Python. Default language is English; French translations in `web/locale/fr/`
- **MQTT topics**: `{device_type}/{device_id}/sensors|status|diag|command|capabilities|commands|calibration|ack`
- **Sensor schema**: narrow table (one row per metric), managed as TimescaleDB hypertable (`managed = False` in Django model, raw SQL migration)
- **TimescaleDB policies**: continuous aggregates (hourly/daily), compression after 7 days, retention 90 days on raw data
- **Roles**: `None` (pending) < `guest` < `resident` < `admin`. Enforced by `@role_required` decorator + `RoleMiddleware`
- **Django admin access**: a superuser can promote/demote other users to full superuser (`is_staff` + `is_superuser`) from `/accounts/users/`. The `.env`-defined superuser (`DJANGO_SUPERUSER_USERNAME`) is protected and never web-modifiable. The admin is re-skinned (`templates/admin/base_site.html` + `static/admin/css/sensor_admin.css`) to the site orange with a permanent red viewport frame.
- **Sensor catalog**: `catalog.SensorDesign` (Markdown body via `catalog/markdown.py`, supports ```mermaid``` fences + uploaded `SensorImage`s), linked to `ota.HardwareCode` (M2M). Firmwares and real devices are derived from the hardware codes, never stored on the design. Edited in Django admin, viewed at `/catalog/`.
- **Templates**: Pico CSS + HTMX (CDN) — no JS framework, no npm. Only JS is ECharts init blocks
- **Static files**: served by WhiteNoise, `collectstatic` runs in entrypoint
- **Device approval**: auto-discovered devices default to `is_approved=False`; sensor data is dropped until an admin approves. Capabilities (hardware ID, metrics, commands, publish interval) are auto-requested on discovery, reconnection, and after every command. Capabilities timeout is `max(60s, 2×publish_interval)` (deep-sleep tolerant); no response → `error` alert.
- **Command lifecycle**: `CommandLog.status` tracks each command: `pending` → `success`/`failed` (from the device `ack` `status`, with optional `response_message`) or `timeout`. `request_capabilities` is logged as pending and resolved to `success` when capabilities arrive, or `timeout`. Wake-up flush re-sends only `pending` commands. Shown as status badges in the device admin command log.
- **Online detection**: computed from `last_seen` and `publish_interval` (offline if no data for 3× interval; default 5 min timeout when interval unknown). `is_online` is a model property, not a DB field.
- **Device health**: capabilities carry `hw` (hardware code → `hw_code`) and `fw` (firmware version → `fw_version`); a device that reports capabilities without either → `needs_firmware_update` (update recommended). Latest `bat_percent` is stored on `Device.battery_percent`; `battery_status` (model property) is `low` ≤20% / `critical` ≤5%. Both surface as UI badges and read-API fields. Thresholds in `devices/models.py`.
- **Status topic**: devices publish alerts (`warning`/`error`) as JSON, not online/offline. Online status is computed server-side.
- **Diagnostics (`diag` topic)**: diag-capable firmware (advertises the `diag` capability flag `"diag":1` → `Device.diag_capable` / `Device.supports_diag`) publishes a health snapshot (`rst`, `miss`, `wake_ms`, `seq`, `pubfail`, `txsent`, `txok`, `rssi`, `heap`, `bat`, `level`, `message`) when health ≥ `warning`, or on demand via `get_diag`. Stored as `DeviceDiagLog`; the `level`/`message` are reflected onto the device alert like a status message. The core diag commands (`get_status`, `get_diag`, `set_confirm_uplink`) are **inferred from the `diag` flag, not the advertised command list** (which no longer lists them); likewise `ota_update` is inferred from the `ota` flag. `get_status` pulls the current alert state (reply on `status`); sent on-demand from the device page and automatically on wake-up. All gated on `supports_diag` (no-op for old firmware). `set_confirm_uplink` toggles the opt-in uplink-delivery confirmation diagnostic (`txok`/`txsent` counters). The diagnostics log is surfaced on the device page (admins) whenever any `DeviceDiagLog` exists, and in full on the admin page. Spec: `docs/mqtt-protocol.md` §6 + firmware `../sensor_iot/docs/diagnostics.md`.
- **Protocol doc**: full MQTT protocol spec in `docs/mqtt-protocol.md`
- **Read-only API**: `/api/v1/` (devices, raw readings, hourly/daily aggregates) for external services. Auth via `Authorization: Bearer <token>` against the `api.ApiKey` model (SHA-256 hashed, managed in Django admin). Exposes approved devices only; query params bounded. Spec in `docs/read-api.md`
- **Security**: CSRF on all forms, logout is POST-only, WebSocket origin validation, MQTT identifier sanitization, `SECURE_*` settings enforced when `DEBUG=False`
- **Logging**: structured JSON to file (`sensor_server.logging.JsonFormatter`), plain text to console
- **Persistent data**: all under `$DATA_DIR` (timescaledb, mosquitto, redis, logs, certs, backups) via bind mounts
- **No generated files in git**: `.env`, mosquitto `passwd`, compiled `.mo` files are all generated at runtime

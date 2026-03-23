# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Project

IoT sensor server for home automation. Receives data from ESP8266 devices over MQTT,
stores time-series in TimescaleDB, serves a real-time dashboard with device control.

## Stack

- **Docker Compose**: mosquitto, timescaledb (pg16), redis, web (Django 5.x / Daphne), nginx
- **Django apps**: `accounts` (auth/roles/OIDC), `devices` (registry/commands), `readings` (time-series/dashboard/WebSocket), `mqtt_bridge` (MQTT worker/auto-discovery)
- **Auth**: optional Authentik SSO via mozilla-django-oidc; local login fallback when `OIDC_RP_CLIENT_ID` is empty
- **Real-time**: Django Channels WebSocket + HTMX ws extension
- **Charts**: ECharts via CDN
- **CSS**: Pico CSS via CDN — no build step
- **i18n**: English (default) + French, `{% trans %}` / `gettext_lazy`, locale files in `web/locale/`
- **Reverse proxy**: Nginx with optional TLS (`NGINX_CONF` env var switches between SSL and no-SSL config)

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
- **MQTT topics**: `{device_type}/{device_id}/sensors|status|command|capabilities`
- **Sensor schema**: narrow table (one row per metric), managed as TimescaleDB hypertable (`managed = False` in Django model, raw SQL migration)
- **TimescaleDB policies**: continuous aggregates (hourly/daily), compression after 7 days, retention 90 days on raw data
- **Roles**: `None` (pending) < `guest` < `resident` < `admin`. Enforced by `@role_required` decorator + `RoleMiddleware`
- **Templates**: Pico CSS + HTMX (CDN) — no JS framework, no npm. Only JS is ECharts init blocks
- **Static files**: served by WhiteNoise, `collectstatic` runs in entrypoint
- **Device approval**: auto-discovered devices default to `is_approved=False`; sensor data is dropped until an admin approves. Capabilities (hardware ID, metrics, commands, publish interval) are auto-requested on discovery, reconnection, and after every command. 60s timeout; no response → `error` alert.
- **Online detection**: computed from `last_seen` and `publish_interval` (offline if no data for 3× interval; default 5 min timeout when interval unknown). `is_online` is a model property, not a DB field.
- **Status topic**: devices publish alerts (`warning`/`error`) as JSON, not online/offline. Online status is computed server-side.
- **Protocol doc**: full MQTT protocol spec in `docs/mqtt-protocol.md`
- **Security**: CSRF on all forms, logout is POST-only, WebSocket origin validation, MQTT identifier sanitization, `SECURE_*` settings enforced when `DEBUG=False`
- **Logging**: structured JSON to file (`sensor_server.logging.JsonFormatter`), plain text to console
- **Persistent data**: all under `$DATA_DIR` (timescaledb, mosquitto, redis, logs, certs, backups) via bind mounts
- **No generated files in git**: `.env`, mosquitto `passwd`, compiled `.mo` files are all generated at runtime

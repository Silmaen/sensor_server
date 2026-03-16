# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

IoT sensor server — Django 5.1 + Channels + TimescaleDB + Mosquitto MQTT + HTMX + ECharts.

## Stack

- **Docker Compose**: mosquitto, timescaledb (pg16), redis, web (Django/Daphne)
- **Django apps**: `accounts` (auth/roles), `devices` (registry/commands), `readings` (time-series), `mqtt_bridge` (MQTT worker)
- **Auth**: Authentik SSO via mozilla-django-oidc, role-based access (guest/resident/admin)
- **Real-time**: Django Channels WebSocket + HTMX ws extension
- **Charts**: ECharts via CDN

## Commands

```bash
# Start everything
docker compose up --build

# Test with simulated sensor
mosquitto_pub -h localhost -t thermo/test1/sensors -m '{"temperature":22.5}' -u backend -P changeme

# Django management (inside container)
docker compose exec web python manage.py shell
docker compose exec web python manage.py createsuperuser
```

## Key conventions

- MQTT topics: `{device_type}/{device_id}/sensors|status|command`
- Sensor data schema: narrow (one row per metric per reading)
- Roles: None (pending) < guest < resident < admin
- Templates use Pico CSS (CDN) + HTMX (CDN) — no JS framework

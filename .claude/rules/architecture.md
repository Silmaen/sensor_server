# Architecture rules

## Django apps
- `accounts` — auth, roles, OIDC backend, middleware. No account management beyond roles.
- `devices` — device registry, command sending via MQTT.
- `readings` — sensor data (TimescaleDB hypertable), dashboard, charts, WebSocket consumers.
- `mqtt_bridge` — MQTT subscriber worker, auto-discovery, ingestion services.

## Database
- TimescaleDB hypertable for sensor readings — `managed = False` in Django, raw SQL migrations.
- Narrow schema: one row per (time, device_id, metric, value).
- Continuous aggregates (hourly/daily) for fast historical queries.
- Compression after 7 days, no retention policy (keep all data).

## Frontend
- Server-side templates only. Pico CSS + HTMX via CDN.
- NO JavaScript frameworks, NO npm, NO build step.
- The only JS allowed is ECharts initialization blocks in templates.

## Infrastructure
- TLS is handled by an external reverse proxy. This app runs HTTP only.
- SECURE_PROXY_SSL_HEADER is always active (not gated on DEBUG).
- Do NOT add nginx, TLS termination, or HTTPS redirect to this project.
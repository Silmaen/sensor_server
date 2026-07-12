# Architecture rules

## Django apps
- `accounts` — auth, roles, OIDC backend, middleware. No account management beyond roles.
- `devices` — device registry, approval workflow, capabilities discovery, command sending via MQTT.
- `readings` — sensor data (TimescaleDB hypertable), dashboard, charts, WebSocket consumers.
- `mqtt_bridge` — MQTT subscriber worker, auto-discovery, ingestion services, capabilities handler.
- `api` — read-only HTTP API (`/api/v1/`) for external services. API-key (Bearer token) auth via `ApiKey` model; exposes approved devices, raw readings, and hourly/daily aggregates. No write endpoints.
- `ota` — hardware registry & firmware catalog. Publication API under `/api/` (CI, `OTA_PUBLISH_TOKEN`); human-facing pages under `/ota/` (`ota.web_urls`, e.g. the firmware overview).
- `catalog` — documentation of designed sensors (`SensorDesign`, Markdown body with Mermaid + uploaded images), M2M to `ota.HardwareCode`. Firmwares and real devices are always derived from the linked hardware codes, never stored on the design. Edited in the Django admin, viewed at `/catalog/`.

## Database
- TimescaleDB hypertable for sensor readings — `managed = False` in Django, raw SQL migrations.
- Narrow schema: one row per (time, device_id, metric, value).
- Continuous aggregates (hourly/daily) for fast historical queries.
- Compression after 7 days, no retention policy (keep all data).

## Frontend
- Server-side templates only. Pico CSS + HTMX via CDN.
- NO JavaScript frameworks, NO npm, NO build step.
- The only JS allowed is ECharts initialization blocks, and the Mermaid init
  block on the catalog detail page (renders ```mermaid``` diagrams from sensor
  docs) — both loaded from a CDN, no bundling.
- The Django admin is re-skinned via `templates/admin/base_site.html` +
  `static/admin/css/sensor_admin.css` (site orange + a permanent red viewport
  frame so the admin is visually unmistakable). CSS only, no admin theme package.

## Infrastructure
- TLS is handled by an external reverse proxy. This app runs HTTP only.
- SECURE_PROXY_SSL_HEADER is always active (not gated on DEBUG).
- nginx MAY be added to the stack to serve Django **static and media** from disk
  (standard Django deployment). Media includes the OTA firmware images (`.bin`).
  Do NOT use it for TLS termination or an HTTPS redirect (TLS stays external).
- Firmware images (OTA) are media, served by nginx (`location /fw/`, from the
  firmware media directory that the publication API writes to). The download
  is unauthenticated by protocol design (trusted filtered LAN, plain HTTP + MD5;
  the `ota_update` `value` URL carries no token) — an exception to the
  authenticated-endpoints rule alongside `/healthz/`.
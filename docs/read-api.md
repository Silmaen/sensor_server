# Read-only HTTP API

This document describes the public, read-only HTTP API exposed for **external
services** that need access to raw sensor data. It covers authentication,
endpoints, query parameters, and response formats.

## Overview

The API is versioned under `/api/v1/` and serves JSON. It is read-only: there is
no endpoint that mutates state. Only data for **approved** devices is exposed.

- Base path: `/api/v1/`
- Authentication: API key via `Authorization: Bearer <token>`
- Format: JSON responses; all timestamps are ISO 8601 with timezone
- Scope: approved devices only; unapproved devices are invisible (404)

## Authentication

Every endpoint requires an API key sent as a Bearer token:

```
Authorization: Bearer sk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Keys are managed by an administrator in the Django admin under **Public API →
API keys**. When a key is created, the raw token is shown **once** and cannot be
recovered afterwards — only its SHA-256 hash is stored. A key can be deactivated
at any time by unchecking *Is active* (no deletion needed). The `last_used_at`
timestamp records the most recent successful request.

Creating a key programmatically:

```python
from api.models import ApiKey
key, raw_token = ApiKey.generate(name="grafana")
print(raw_token)  # shown once — store it securely
```

Failed authentication returns `401`:

```json
{"error": "unauthorized", "detail": "Invalid or inactive API key."}
```

## Common query parameters

| Parameter   | Applies to            | Default        | Notes                                             |
|-------------|-----------------------|----------------|---------------------------------------------------|
| `device_id` | readings, aggregates  | all approved   | Filter to one device. Unknown/unapproved → `404`. |
| `metric`    | readings, aggregates  | all metrics    | Filter to a single metric name.                   |
| `start`     | readings, aggregates  | `end` − 1 day  | ISO 8601. Naive datetimes assumed server tz.      |
| `end`       | readings, aggregates  | now            | ISO 8601.                                         |
| `limit`     | readings, aggregates  | `1000`         | Clamped to `1..10000`.                            |

Constraints (return `400` when violated):

- `start` must be strictly before `end`.
- The `end − start` span must not exceed **366 days**.

Pagination: results are ordered ascending (by time/bucket). When `has_more` is
`true`, more rows exist beyond `limit`; fetch the next page by setting `start`
to the last returned timestamp.

## Endpoints

### `GET /api/v1/devices/`

List approved devices with their advertised metrics and units.

```json
{
  "count": 1,
  "devices": [
    {
      "device_id": "thermo_1",
      "device_type": "thermo",
      "display_name": "Living room",
      "location": "",
      "location_type": "indoor",
      "is_online": true,
      "last_seen": "2026-07-06T15:16:10.756345+00:00",
      "publish_interval": 300,
      "metrics": ["temperature", "humidity"],
      "units": {"temperature": "°C", "humidity": "%"}
    }
  ]
}
```

### `GET /api/v1/readings/`

Raw sensor readings from the time-series table, ascending by time.

Query params: `device_id`, `metric`, `start`, `end`, `limit`.

```json
{
  "count": 2,
  "has_more": false,
  "readings": [
    {"time": "2026-07-06T15:15:10.756345+00:00", "device_id": "thermo_1", "metric": "temperature", "value": 20.4},
    {"time": "2026-07-06T15:16:10.756345+00:00", "device_id": "thermo_1", "metric": "temperature", "value": 20.5}
  ]
}
```

> Raw data is retained for 90 days. For longer history use the aggregates
> endpoint, which is kept indefinitely.

### `GET /api/v1/aggregates/`

Hourly or daily continuous-aggregate buckets (average, min, max, sample count).

Query params: `resolution` (`hourly` | `daily`, default `hourly`), `device_id`,
`metric`, `start`, `end`, `limit`.

```json
{
  "resolution": "hourly",
  "count": 1,
  "has_more": false,
  "aggregates": [
    {
      "bucket": "2026-07-06T15:00:00+00:00",
      "device_id": "thermo_1",
      "metric": "temperature",
      "avg": 20.45,
      "min": 20.4,
      "max": 20.5,
      "samples": 12
    }
  ]
}
```

## Error responses

| Status | Body `error`   | When                                            |
|--------|----------------|-------------------------------------------------|
| `400`  | `bad_request`  | Invalid timestamps, span too large, bad `resolution`. |
| `401`  | `unauthorized` | Missing, invalid, or inactive API key.          |
| `404`  | `not_found`    | `device_id` is unknown or not approved.         |

## Example

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "https://your-host/api/v1/readings/?device_id=thermo_1&metric=temperature&start=2026-07-01T00:00:00Z&limit=500"
```

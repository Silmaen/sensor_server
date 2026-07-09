"""Read-only public API (v1) for external services.

Every endpoint requires an API key (``Authorization: Bearer <token>``) and only
exposes data for approved devices. Responses are JSON. Query parameters are
validated and bounded to protect the database.
"""

import logging
from datetime import timedelta

from django.db import connection
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from devices.models import Device
from readings.models import SensorReading

from .auth import api_key_required

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 1000
MAX_LIMIT = 10000
DEFAULT_RANGE = timedelta(days=1)
MAX_RANGE = timedelta(days=366)

AGGREGATE_TABLES = {
    "hourly": "readings_hourly",
    "daily": "readings_daily",
}


def _bad_request(detail):
    return JsonResponse({"error": "bad_request", "detail": detail}, status=400)


def _not_found(detail):
    return JsonResponse({"error": "not_found", "detail": detail}, status=404)


def _parse_limit(request):
    """Return a limit clamped to [1, MAX_LIMIT], defaulting to DEFAULT_LIMIT."""
    try:
        limit = int(request.GET.get("limit", DEFAULT_LIMIT))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


def _parse_range(request):
    """Parse and validate the time window.

    Returns ``(start, end, None)`` on success or ``(None, None, error)`` on
    failure. Both bounds default sensibly and the span is capped at MAX_RANGE.
    """
    end_raw = request.GET.get("end")
    start_raw = request.GET.get("start")

    end = parse_datetime(end_raw) if end_raw else timezone.now()
    if end is None:
        return None, None, "Invalid 'end' timestamp (expected ISO 8601)."
    start = parse_datetime(start_raw) if start_raw else end - DEFAULT_RANGE
    if start is None:
        return None, None, "Invalid 'start' timestamp (expected ISO 8601)."

    if timezone.is_naive(start):
        start = timezone.make_aware(start)
    if timezone.is_naive(end):
        end = timezone.make_aware(end)

    if start >= end:
        return None, None, "'start' must be strictly before 'end'."
    if end - start > MAX_RANGE:
        return None, None, f"Time range exceeds the maximum of {MAX_RANGE.days} days."
    return start, end, None


def _approved_device_ids():
    return list(
        Device.objects.filter(is_approved=True).values_list("device_id", flat=True)
    )


def _resolve_devices(request):
    """Resolve the device_id filter against approved devices.

    Returns ``(device_ids, None)`` on success or ``(None, error_response)`` if
    an explicit device_id is unknown or not approved.
    """
    device_id = request.GET.get("device_id")
    if device_id:
        if not Device.objects.filter(device_id=device_id, is_approved=True).exists():
            return None, _not_found("Unknown or unapproved device.")
        return [device_id], None
    return _approved_device_ids(), None


# ---------------------------------------------------------------------------
# GET /api/v1/devices/
# ---------------------------------------------------------------------------

@api_key_required
def devices_view(request):
    """List approved devices with their advertised metrics and units."""
    devices = Device.objects.filter(is_approved=True)
    data = []
    for d in devices:
        caps = d.capabilities or {}
        data.append({
            "device_id": d.device_id,
            "device_type": d.device_type,
            "display_name": d.effective_name,
            "location": d.location,
            "location_type": d.location_type,
            "is_online": d.is_online,
            "last_seen": d.last_seen.isoformat() if d.last_seen else None,
            "publish_interval": d.publish_interval,
            "hardware_id": d.hardware_id,
            "hw_code": d.hardware_code_id or "",
            "hw_rev": d.hw_rev,
            "ota_capable": d.ota_capable,
            "fw_version": d.fw_version,
            "battery_percent": d.battery_percent,
            "battery_status": d.battery_status,
            "needs_firmware_update": d.needs_firmware_update,
            "is_legacy_firmware": d.is_legacy_firmware,
            "metrics": caps.get("metrics", []),
            "units": caps.get("units", {}),
        })
    return JsonResponse({"count": len(data), "devices": data})


# ---------------------------------------------------------------------------
# GET /api/v1/readings/
# ---------------------------------------------------------------------------

@api_key_required
def readings_view(request):
    """Return raw sensor readings, ascending by time.

    Query params: ``device_id``, ``metric``, ``start``, ``end``, ``limit``.
    Results are ordered by time so ``end``/``start`` can be used as a cursor;
    ``has_more`` signals that more rows exist beyond ``limit``.
    """
    start, end, err = _parse_range(request)
    if err:
        return _bad_request(err)

    device_ids, error = _resolve_devices(request)
    if error:
        return error

    qs = SensorReading.objects.filter(
        device_id__in=device_ids, time__gte=start, time__lte=end
    )
    metric = request.GET.get("metric")
    if metric:
        qs = qs.filter(metric=metric)

    limit = _parse_limit(request)
    rows = list(
        qs.order_by("time", "device_id", "metric")
        .values_list("time", "device_id", "metric", "value")[: limit + 1]
    )
    has_more = len(rows) > limit
    rows = rows[:limit]

    readings = [
        {"time": t.isoformat(), "device_id": did, "metric": m, "value": v}
        for t, did, m, v in rows
    ]
    return JsonResponse({
        "count": len(readings),
        "has_more": has_more,
        "readings": readings,
    })


# ---------------------------------------------------------------------------
# GET /api/v1/aggregates/
# ---------------------------------------------------------------------------

@api_key_required
def aggregates_view(request):
    """Return hourly or daily continuous-aggregate buckets.

    Query params: ``resolution`` (``hourly``|``daily``, default ``hourly``),
    ``device_id``, ``metric``, ``start``, ``end``, ``limit``.
    """
    resolution = request.GET.get("resolution", "hourly")
    table = AGGREGATE_TABLES.get(resolution)
    if table is None:
        return _bad_request("'resolution' must be 'hourly' or 'daily'.")

    start, end, err = _parse_range(request)
    if err:
        return _bad_request(err)

    device_ids, error = _resolve_devices(request)
    if error:
        return error

    limit = _parse_limit(request)
    params = [device_ids, start, end]
    metric_clause = ""
    metric = request.GET.get("metric")
    if metric:
        metric_clause = "AND metric = %s"
        params.append(metric)
    params.append(limit + 1)

    # `table` comes from the AGGREGATE_TABLES whitelist, so interpolation is safe.
    sql = (
        "SELECT bucket, device_id, metric, avg_value, min_value, max_value, sample_count "
        f"FROM {table} "
        "WHERE device_id = ANY(%s) AND bucket >= %s AND bucket <= %s "
        f"{metric_clause} "
        "ORDER BY bucket, device_id, metric LIMIT %s"
    )

    rows = []
    if device_ids:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()

    has_more = len(rows) > limit
    rows = rows[:limit]

    aggregates = [
        {
            "bucket": bucket.isoformat(),
            "device_id": did,
            "metric": m,
            "avg": round(avg, 4) if avg is not None else None,
            "min": round(mn, 4) if mn is not None else None,
            "max": round(mx, 4) if mx is not None else None,
            "samples": cnt,
        }
        for bucket, did, m, avg, mn, mx, cnt in rows
    ]
    return JsonResponse({
        "resolution": resolution,
        "count": len(aggregates),
        "has_more": has_more,
        "aggregates": aggregates,
    })

import json
import logging
from collections import defaultdict
from datetime import timedelta

from django.db import connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _

from accounts.decorators import role_required
from devices.models import Device, DeviceStatusLog

from .metrics import get_metrics_display_map
from .models import SensorReading

logger = logging.getLogger(__name__)

PRESETS = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "1d": timedelta(days=1),
    "7d": timedelta(days=7),
    "1m": timedelta(days=30),
    "2m": timedelta(days=60),
    "3m": timedelta(days=90),
    "1y": timedelta(days=365),
}


def _is_guest_only(request):
    """Return True if the user has only guest-level access."""
    if request.user.is_superuser:
        return False
    profile = getattr(request.user, "profile", None)
    if profile and profile.has_role("resident"):
        return False
    return True


def _parse_time_range(request):
    """Parse preset or start/end from request GET params. Returns (start, end)."""
    preset = request.GET.get("preset")
    if preset and preset in PRESETS:
        end = timezone.now()
        start = end - PRESETS[preset]
        return start, end
    start = parse_datetime(request.GET.get("start", ""))
    end = parse_datetime(request.GET.get("end", ""))
    if not start or not end:
        end = timezone.now()
        start = end - timedelta(hours=24)
    return start, end


def _fetch_readings(device_id, metric, start, end):
    """Fetch readings for a single device/metric, auto-selecting data source by span."""
    span_seconds = (end - start).total_seconds()

    if span_seconds <= 48 * 3600:
        readings = (
            SensorReading.objects.filter(
                device_id=device_id, metric=metric,
                time__gte=start, time__lte=end,
            )
            .order_by("time")
            .values_list("time", "value")
        )
        return [r[0].isoformat() for r in readings], [r[1] for r in readings]

    table = "readings_hourly" if span_seconds <= 90 * 86400 else "readings_daily"
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT bucket, avg_value FROM {table}
            WHERE device_id = %s AND metric = %s
              AND bucket >= %s AND bucket <= %s
            ORDER BY bucket
            """,
            [device_id, metric, start, end],
        )
        rows = cursor.fetchall()
    return [r[0].isoformat() for r in rows], [round(r[1], 2) for r in rows]


def _annotate_devices_with_metrics(devices, guest_only):
    """Fetch latest readings and attach latest_metrics + visible_metrics_csv to each device."""
    device_ids = list(devices.values_list("device_id", flat=True))

    latest_readings = {}
    if device_ids:
        qs = SensorReading.objects.raw(
            """
            SELECT DISTINCT ON (device_id, metric)
                time, device_id, metric, value
            FROM readings_sensorreading
            WHERE device_id = ANY(%s)
            ORDER BY device_id, metric, time DESC
            """,
            [device_ids],
        )
        for r in qs:
            latest_readings.setdefault(r.device_id, {})[r.metric] = round(r.value, 1)

    for device in devices:
        all_metrics = latest_readings.get(device.device_id, {})
        if guest_only:
            visible = device.guest_visible_metrics or []
            device.latest_metrics = {
                k: v for k, v in all_metrics.items() if k in visible
            }
        else:
            device.latest_metrics = all_metrics
        device.visible_metrics_csv = ",".join(device.latest_metrics.keys())


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def _compute_averages(devices):
    """Compute per-metric averages grouped by location_type (indoor/outdoor)."""
    # {group: {metric: [values]}}
    groups = {"indoor": defaultdict(list), "outdoor": defaultdict(list)}
    for d in devices:
        grp = d.location_type if d.location_type in groups else None
        if grp is None:
            continue
        for metric, value in d.latest_metrics.items():
            groups[grp][metric].append(value)
    # {group: {metric: avg}}
    averages = {}
    for grp, metrics in groups.items():
        avgs = {}
        for metric, values in metrics.items():
            avgs[metric] = round(sum(values) / len(values), 1)
        if avgs:
            averages[grp] = avgs
    return averages


@role_required("guest")
def dashboard_view(request):
    devices = Device.objects.filter(is_approved=True)
    _annotate_devices_with_metrics(devices, _is_guest_only(request))
    all_metrics = set()
    for d in devices:
        all_metrics.update(d.latest_metrics.keys())
    metrics_display = get_metrics_display_map(all_metrics)
    averages = _compute_averages(devices)
    return render(request, "readings/dashboard.html", {
        "devices": devices,
        "metrics_display_json": json.dumps(metrics_display),
        "averages": averages,
    })


@role_required("guest")
def dashboard_cards_view(request):
    """HTMX partial: return only the device card grid for auto-refresh."""
    devices = Device.objects.filter(is_approved=True)
    _annotate_devices_with_metrics(devices, _is_guest_only(request))
    averages = _compute_averages(devices)
    return render(request, "readings/_dashboard_cards.html", {
        "devices": devices,
        "averages": averages,
    })


# ---------------------------------------------------------------------------
# Chart data API (single device, single metric)
# ---------------------------------------------------------------------------

@role_required("guest")
def chart_data_view(request, device_id):
    device = get_object_or_404(Device, device_id=device_id, is_approved=True)
    metric = request.GET.get("metric", "temperature")

    if _is_guest_only(request):
        if metric not in (device.guest_visible_metrics or []):
            return JsonResponse({"error": "forbidden"}, status=403)

    start, end = _parse_time_range(request)
    times, values = _fetch_readings(device_id, metric, start, end)
    unit = (device.capabilities or {}).get("units", {}).get(metric, "")

    return JsonResponse({
        "times": times,
        "values": values,
        "metric": metric,
        "device_id": device_id,
        "unit": unit,
    })


# ---------------------------------------------------------------------------
# Status timeline API
# ---------------------------------------------------------------------------

@role_required("guest")
def status_timeline_view(request, device_id):
    device = get_object_or_404(Device, device_id=device_id, is_approved=True)
    start, end = _parse_time_range(request)

    # Get the initial state: last log entry before the window
    initial = (
        DeviceStatusLog.objects
        .filter(device=device, time__lt=start)
        .order_by("-time")
        .values_list("alert_level", "alert_message")
        .first()
    )
    initial_level = initial[0] if initial else ""
    initial_message = initial[1] if initial else ""

    # Get all log entries within the window
    logs = list(
        DeviceStatusLog.objects
        .filter(device=device, time__gte=start, time__lte=end)
        .order_by("time")
        .values_list("time", "alert_level", "alert_message")
    )

    segments = []
    current_start = start.isoformat()
    current_level = initial_level
    current_message = initial_message

    for log_time, level, message in logs:
        segments.append({
            "start": current_start,
            "end": log_time.isoformat(),
            "level": current_level or "ok",
            "message": current_message,
        })
        current_start = log_time.isoformat()
        current_level = level
        current_message = message

    # Final segment to end of window
    segments.append({
        "start": current_start,
        "end": end.isoformat(),
        "level": current_level or "ok",
        "message": current_message,
    })

    return JsonResponse({"segments": segments})


# ---------------------------------------------------------------------------
# Overview page (all sensors by metric type)
# ---------------------------------------------------------------------------

@role_required("guest")
def overview_view(request):
    devices = Device.objects.filter(is_approved=True)
    guest_only = _is_guest_only(request)

    # Gather all distinct metrics across devices
    device_ids = list(devices.values_list("device_id", flat=True))
    all_metrics_qs = (
        SensorReading.objects
        .filter(device_id__in=device_ids)
        .values_list("metric", flat=True)
        .distinct()
        .order_by("metric")
    )

    # Build metric -> device list mapping
    metrics_devices = defaultdict(list)
    device_names = {}
    units = {}

    for device in devices:
        device_names[device.device_id] = device.effective_name
        caps = device.capabilities or {}
        for k, v in caps.get("units", {}).items():
            if k not in units:
                units[k] = v

    # For each metric, find which devices report it
    device_metrics = {}
    if device_ids:
        qs = SensorReading.objects.raw(
            """
            SELECT DISTINCT ON (device_id, metric)
                time, device_id, metric, value
            FROM readings_sensorreading
            WHERE device_id = ANY(%s)
            ORDER BY device_id, metric, time DESC
            """,
            [device_ids],
        )
        for r in qs:
            device_metrics.setdefault(r.device_id, set()).add(r.metric)

    for metric in all_metrics_qs:
        for device in devices:
            dm = device_metrics.get(device.device_id, set())
            if metric not in dm:
                continue
            if guest_only:
                visible = device.guest_visible_metrics or []
                if metric not in visible:
                    continue
            metrics_devices[metric].append(device.device_id)

    sorted_metrics = sorted(metrics_devices.keys())
    metrics_display = get_metrics_display_map(sorted_metrics)
    # Merge capability units (capabilities take precedence)
    for m, u in units.items():
        if m in metrics_display and u:
            metrics_display[m]["unit"] = u

    return render(request, "readings/overview.html", {
        "metrics_devices_json": json.dumps(dict(metrics_devices)),
        "device_names_json": json.dumps(device_names),
        "units_json": json.dumps(units),
        "metrics_display_json": json.dumps(metrics_display),
        "metrics": sorted_metrics,
        "devices": devices,
    })


@role_required("guest")
def overview_chart_data_view(request):
    """Return chart data for one metric across multiple devices."""
    metric = request.GET.get("metric", "")
    device_ids = [d for d in request.GET.get("devices", "").split(",") if d]
    if not metric or not device_ids:
        return JsonResponse({"series": {}, "metric": metric, "unit": ""})

    start, end = _parse_time_range(request)
    span_seconds = (end - start).total_seconds()

    # Determine source
    if span_seconds <= 48 * 3600:
        readings = (
            SensorReading.objects.filter(
                device_id__in=device_ids, metric=metric,
                time__gte=start, time__lte=end,
            )
            .order_by("device_id", "time")
            .values_list("device_id", "time", "value")
        )
        series = defaultdict(lambda: {"times": [], "values": []})
        for did, t, v in readings:
            series[did]["times"].append(t.isoformat())
            series[did]["values"].append(v)
    else:
        table = "readings_hourly" if span_seconds <= 90 * 86400 else "readings_daily"
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT device_id, bucket, avg_value FROM {table}
                WHERE device_id = ANY(%s) AND metric = %s
                  AND bucket >= %s AND bucket <= %s
                ORDER BY device_id, bucket
                """,
                [device_ids, metric, start, end],
            )
            rows = cursor.fetchall()
        series = defaultdict(lambda: {"times": [], "values": []})
        for did, t, v in rows:
            series[did]["times"].append(t.isoformat())
            series[did]["values"].append(round(v, 2))

    # Get unit from first device that has it
    unit = ""
    for did in device_ids:
        try:
            d = Device.objects.get(device_id=did)
            u = (d.capabilities or {}).get("units", {}).get(metric, "")
            if u:
                unit = u
                break
        except Device.DoesNotExist:
            continue

    return JsonResponse({
        "metric": metric,
        "unit": unit,
        "series": dict(series),
    })


# ---------------------------------------------------------------------------
# Delete readings (admin only)
# ---------------------------------------------------------------------------

@role_required("admin")
def delete_readings_view(request, device_id):
    """Delete aberrant readings for a device within a time range."""
    device = get_object_or_404(Device, device_id=device_id)

    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    metric = request.POST.get("metric", "").strip()
    start = parse_datetime(request.POST.get("start", ""))
    end = parse_datetime(request.POST.get("end", ""))

    if not start or not end or start >= end:
        return JsonResponse({"error": _("Invalid time range.")}, status=400)

    filters = {"device_id": device_id, "time__gte": start, "time__lte": end}
    if metric:
        filters["metric"] = metric

    # Decompress any compressed chunks overlapping the time range
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT decompress_chunk(i.chunk_name::regclass, if_compressed => true)
            FROM timescaledb_information.chunks i
            WHERE i.hypertable_name = 'readings_sensorreading'
              AND i.is_compressed = true
              AND i.range_start::timestamptz <= %s
              AND i.range_end::timestamptz >= %s;
            """,
            [end, start],
        )

    count = SensorReading.objects.filter(**filters).count()
    if count == 0:
        return JsonResponse({"deleted": 0, "message": _("No readings found in this range.")})

    SensorReading.objects.filter(**filters).delete()

    # Refresh continuous aggregates for the affected range
    with connection.cursor() as cursor:
        for view_name in ("readings_hourly", "readings_daily"):
            cursor.execute(
                f"CALL refresh_continuous_aggregate('{view_name}', %s::timestamptz, %s::timestamptz);",
                [start, end],
            )

    logger.info(
        "Deleted %d readings for device %s (metric=%s, range=%s to %s) by user %s",
        count, device_id, metric or "ALL", start, end, request.user,
    )

    return JsonResponse({
        "deleted": count,
        "message": _("%(count)d readings deleted.") % {"count": count},
    })

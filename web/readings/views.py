from datetime import timedelta

from django.db import connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.decorators import role_required
from devices.models import Device

from .models import SensorReading

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
        # CSV for JS filtering of WebSocket-pushed metrics
        device.visible_metrics_csv = ",".join(device.latest_metrics.keys())


@role_required("guest")
def dashboard_view(request):
    devices = Device.objects.filter(is_approved=True)
    _annotate_devices_with_metrics(devices, _is_guest_only(request))
    return render(request, "readings/dashboard.html", {"devices": devices})


@role_required("guest")
def dashboard_cards_view(request):
    """HTMX partial: return only the device card grid for auto-refresh."""
    devices = Device.objects.filter(is_approved=True)
    _annotate_devices_with_metrics(devices, _is_guest_only(request))
    return render(request, "readings/_dashboard_cards.html", {"devices": devices})


@role_required("guest")
def chart_data_view(request, device_id):
    device = get_object_or_404(Device, device_id=device_id, is_approved=True)
    metric = request.GET.get("metric", "temperature")

    # Guest visibility check
    if _is_guest_only(request):
        if metric not in (device.guest_visible_metrics or []):
            return JsonResponse({"error": "forbidden"}, status=403)

    # Determine time range
    preset = request.GET.get("preset")
    if preset and preset in PRESETS:
        end = timezone.now()
        start = end - PRESETS[preset]
    else:
        start = parse_datetime(request.GET.get("start", ""))
        end = parse_datetime(request.GET.get("end", ""))
        if not start or not end:
            end = timezone.now()
            start = end - timedelta(hours=24)

    span_seconds = (end - start).total_seconds()

    if span_seconds <= 48 * 3600:
        # Raw data
        readings = (
            SensorReading.objects.filter(
                device_id=device_id, metric=metric,
                time__gte=start, time__lte=end,
            )
            .order_by("time")
            .values_list("time", "value")
        )
        times = [r[0].isoformat() for r in readings]
        values = [r[1] for r in readings]
    elif span_seconds <= 90 * 86400:
        # Hourly aggregate
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT bucket, avg_value
                FROM readings_hourly
                WHERE device_id = %s AND metric = %s
                  AND bucket >= %s AND bucket <= %s
                ORDER BY bucket
                """,
                [device_id, metric, start, end],
            )
            rows = cursor.fetchall()
        times = [r[0].isoformat() for r in rows]
        values = [round(r[1], 2) for r in rows]
    else:
        # Daily aggregate
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT bucket, avg_value
                FROM readings_daily
                WHERE device_id = %s AND metric = %s
                  AND bucket >= %s AND bucket <= %s
                ORDER BY bucket
                """,
                [device_id, metric, start, end],
            )
            rows = cursor.fetchall()
        times = [r[0].isoformat() for r in rows]
        values = [round(r[1], 2) for r in rows]

    return JsonResponse({
        "times": times,
        "values": values,
        "metric": metric,
        "device_id": device_id,
    })

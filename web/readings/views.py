import json
from datetime import timedelta

from django.db.models import Avg
from django.db.models.functions import TruncHour
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from accounts.decorators import role_required
from devices.models import Device

from .models import SensorReading


@role_required("guest")
def dashboard_view(request):
    devices = Device.objects.filter(is_approved=True)
    return render(request, "readings/dashboard.html", {"devices": devices})


@role_required("guest")
def chart_data_view(request, device_id):
    try:
        hours = min(max(int(request.GET.get("hours", 24)), 1), 8760)
    except (ValueError, TypeError):
        hours = 24
    metric = request.GET.get("metric", "temperature")
    since = timezone.now() - timedelta(hours=hours)

    readings = (
        SensorReading.objects.filter(
            device_id=device_id, metric=metric, time__gte=since
        )
        .order_by("time")
        .values_list("time", "value")
    )

    data = {
        "times": [r[0].isoformat() for r in readings],
        "values": [r[1] for r in readings],
        "metric": metric,
        "device_id": device_id,
    }
    return JsonResponse(data)


@role_required("guest")
def chart_data_hourly_view(request, device_id):
    try:
        days = min(max(int(request.GET.get("days", 7)), 1), 365)
    except (ValueError, TypeError):
        days = 7
    metric = request.GET.get("metric", "temperature")
    since = timezone.now() - timedelta(days=days)

    readings = (
        SensorReading.objects.filter(
            device_id=device_id, metric=metric, time__gte=since
        )
        .annotate(hour=TruncHour("time"))
        .values("hour")
        .annotate(avg_value=Avg("value"))
        .order_by("hour")
    )

    data = {
        "times": [r["hour"].isoformat() for r in readings],
        "values": [round(r["avg_value"], 2) for r in readings],
        "metric": metric,
        "device_id": device_id,
    }
    return JsonResponse(data)

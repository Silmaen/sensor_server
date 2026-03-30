import json
import logging

from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.decorators import role_required
from mqtt_bridge.services import _mqtt_publish, request_capabilities
from readings.models import SensorReading

from .models import CommandLog, Device

logger = logging.getLogger(__name__)


def _get_device_metrics(device):
    """Return sorted list of distinct metrics for a device."""
    metrics = list(
        SensorReading.objects.filter(device_id=device.device_id)
        .values_list("metric", flat=True)
        .distinct()
        .order_by("metric")
    )
    if not metrics and device.capabilities and device.capabilities.get("metrics"):
        metrics = sorted(device.capabilities["metrics"])
    return metrics


@role_required("guest")
def device_list_view(request):
    devices = Device.objects.all()
    pending_devices = devices.filter(is_approved=False)
    approved_devices = devices.filter(is_approved=True)
    return render(request, "devices/device_list.html", {
        "devices": devices,
        "pending_devices": pending_devices,
        "approved_devices": approved_devices,
    })


@role_required("guest")
def device_history_view(request, device_id):
    device = get_object_or_404(Device, device_id=device_id, is_approved=True)
    metrics = _get_device_metrics(device)

    # Filter for guest visibility
    is_resident_plus = request.user.is_superuser or (
        hasattr(request.user, "profile") and request.user.profile.has_role("resident")
    )
    if not is_resident_plus:
        visible = device.guest_visible_metrics or []
        metrics = [m for m in metrics if m in visible]

    is_admin = request.user.is_superuser or (
        hasattr(request.user, "profile") and request.user.profile.has_role("admin")
    )

    units = device.capabilities.get("units", {}) if device.capabilities else {}

    return render(request, "devices/device_history.html", {
        "device": device,
        "metrics": metrics,
        "metrics_json": json.dumps(metrics),
        "units_json": json.dumps(units),
        "is_admin": is_admin,
    })


@role_required("admin")
def device_admin_view(request, device_id):
    device = get_object_or_404(Device, device_id=device_id)
    commands = device.commands.select_related("sent_by")[:20]
    command_params = (device.capabilities or {}).get("command_params", {})
    return render(request, "devices/device_admin.html", {
        "device": device,
        "commands": commands,
        "command_params_json": json.dumps(command_params),
    })


@role_required("admin")
def device_edit_view(request, device_id):
    device = get_object_or_404(Device, device_id=device_id)
    metrics = _get_device_metrics(device)

    if request.method == "POST":
        device.display_name = request.POST.get("display_name", "")
        device.location = request.POST.get("location", "")
        device.guest_visible_metrics = request.POST.getlist("guest_visible_metrics")
        try:
            interval = int(request.POST.get("publish_interval", 0))
            device.publish_interval = max(0, min(interval, 86400))
        except (ValueError, TypeError):
            pass
        device.save(update_fields=["display_name", "location", "guest_visible_metrics", "publish_interval"])
        if request.headers.get("HX-Request"):
            return render(request, "devices/_device_card.html", {"device": device})
        return redirect("devices:admin", device_id=device.device_id)

    return render(request, "devices/device_edit.html", {
        "device": device,
        "metrics": metrics,
    })


@role_required("resident")
def device_command_view(request, device_id):
    device = get_object_or_404(Device, device_id=device_id)
    if request.method != "POST":
        return HttpResponseBadRequest()

    command_str = request.POST.get("command", "").strip()
    if not command_str:
        return HttpResponseBadRequest(_("Empty command."))

    try:
        command_data = json.loads(command_str)
    except json.JSONDecodeError:
        command_data = {"action": command_str}

    topic = f"{device.device_type}/{device.device_id}/command"
    payload = json.dumps(command_data)

    try:
        _mqtt_publish(topic, payload, retain=True)
    except Exception:
        logger.exception("Failed to publish MQTT command to %s", topic)
        return HttpResponseBadRequest(_("MQTT publish error."))

    CommandLog.objects.create(
        device=device,
        command=command_data,
        sent_by=request.user,
    )

    # Re-request capabilities as the command may have changed them
    request_capabilities(device)

    if request.headers.get("HX-Request"):
        commands = device.commands.select_related("sent_by")[:20]
        return render(request, "devices/_command_log.html", {"commands": commands, "device": device})

    return redirect("devices:admin", device_id=device.device_id)


@role_required("admin")
def device_delete_command_view(request, device_id, command_id):
    device = get_object_or_404(Device, device_id=device_id)
    if request.method != "POST":
        return HttpResponseBadRequest()
    CommandLog.objects.filter(id=command_id, device=device).delete()
    if request.headers.get("HX-Request"):
        commands = device.commands.select_related("sent_by")[:20]
        return render(request, "devices/_command_log.html", {
            "commands": commands, "device": device,
        })
    return redirect("devices:admin", device_id=device.device_id)


@role_required("admin")
def device_clear_commands_view(request, device_id):
    device = get_object_or_404(Device, device_id=device_id)
    if request.method != "POST":
        return HttpResponseBadRequest()
    device.commands.all().delete()
    if request.headers.get("HX-Request"):
        return render(request, "devices/_command_log.html", {
            "commands": [], "device": device,
        })
    return redirect("devices:admin", device_id=device.device_id)


@role_required("admin")
def device_approve_view(request, device_id):
    device = get_object_or_404(Device, device_id=device_id)
    if request.method != "POST":
        return HttpResponseBadRequest()

    action = request.POST.get("action", "")
    if action == "approve":
        device.is_approved = True
        device.save(update_fields=["is_approved"])
    elif action == "revoke":
        device.is_approved = False
        device.save(update_fields=["is_approved"])

    if request.headers.get("HX-Request"):
        pending_devices = Device.objects.filter(is_approved=False)
        approved_devices = Device.objects.filter(is_approved=True)
        return render(request, "devices/_device_tables.html", {
            "pending_devices": pending_devices,
            "approved_devices": approved_devices,
        })

    return redirect("devices:list")


@role_required("admin")
def device_request_capabilities_view(request, device_id):
    device = get_object_or_404(Device, device_id=device_id)
    if request.method != "POST":
        return HttpResponseBadRequest()

    request_capabilities(device)

    CommandLog.objects.create(
        device=device,
        command={"action": "request_capabilities"},
        sent_by=request.user,
        acked=True,
        acked_at=timezone.now(),
    )

    if request.headers.get("HX-Request"):
        commands = device.commands.select_related("sent_by")[:20]
        return render(request, "devices/_command_log.html", {"commands": commands, "device": device})

    return redirect("devices:admin", device_id=device.device_id)

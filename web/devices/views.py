import json
import logging
import re

from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.decorators import role_required
from mqtt_bridge.services import _mqtt_publish, request_capabilities
from readings.metrics import get_metrics_display_map
from readings.models import SensorReading

from .models import CommandLog, Device, DeviceStatusLog

SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

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
    metrics_display = get_metrics_display_map(metrics)
    # Merge capability units into display map (capabilities take precedence)
    for m, u in units.items():
        if m in metrics_display and u:
            metrics_display[m]["unit"] = u

    return render(request, "devices/device_history.html", {
        "device": device,
        "metrics": metrics,
        "metrics_json": json.dumps(metrics),
        "units_json": json.dumps(units),
        "metrics_display_json": json.dumps(metrics_display),
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


@role_required("admin")
def device_delete_view(request, device_id):
    device = get_object_or_404(Device, device_id=device_id)

    if request.method == "POST":
        with transaction.atomic():
            # SensorReading is not FK-linked, delete manually
            SensorReading.objects.filter(device_id=device.device_id).delete()
            # DeviceStatusLog and CommandLog cascade via FK
            device.delete()
        return redirect("devices:list")

    reading_count = SensorReading.objects.filter(device_id=device.device_id).count()
    return render(request, "devices/device_delete_confirm.html", {
        "device": device,
        "reading_count": reading_count,
    })


@role_required("admin")
def device_rename_view(request, device_id):
    device = get_object_or_404(Device, device_id=device_id)

    if request.method != "POST":
        return render(request, "devices/device_rename.html", {"device": device})

    new_id = request.POST.get("new_device_id", "").strip()
    if not new_id:
        return render(request, "devices/device_rename.html", {
            "device": device,
            "error": _("Device ID cannot be empty."),
        })
    if not SAFE_IDENTIFIER_RE.match(new_id):
        return render(request, "devices/device_rename.html", {
            "device": device,
            "error": _("Device ID may only contain letters, digits, hyphens, and underscores."),
        })
    if new_id == device.device_id:
        return redirect("devices:admin", device_id=device.device_id)

    # Check if target device already exists
    try:
        target = Device.objects.get(device_id=new_id)
    except Device.DoesNotExist:
        target = None

    if target is None:
        # Simple rename — no conflict
        _rename_device(device, new_id)
        return redirect("devices:admin", device_id=new_id)

    # Conflict: target exists — need merge confirmation
    confirm = request.POST.get("confirm_merge")
    if confirm == "yes":
        _merge_devices(source=device, target=target)
        return redirect("devices:admin", device_id=target.device_id)

    # Show merge confirmation page
    return render(request, "devices/device_rename_merge_confirm.html", {
        "device": device,
        "target": target,
        "new_device_id": new_id,
    })


def _rename_device(device, new_id):
    """Rename a device by creating a copy with the new ID and migrating all data."""
    old_id = device.device_id
    with transaction.atomic():
        # Update sensor readings (CharField, not FK)
        SensorReading.objects.filter(device_id=old_id).update(device_id=new_id)
        # Create new device with new PK, copying all fields
        Device.objects.create(
            device_id=new_id,
            device_type=device.device_type,
            display_name=device.display_name,
            location=device.location,
            config=device.config,
            last_seen=device.last_seen,
            is_approved=device.is_approved,
            hardware_id=device.hardware_id,
            capabilities=device.capabilities,
            publish_interval=device.publish_interval,
            alert_level=device.alert_level,
            alert_message=device.alert_message,
            capabilities_requested_at=device.capabilities_requested_at,
            guest_visible_metrics=device.guest_visible_metrics,
        )
        new_device = Device.objects.get(device_id=new_id)
        # Re-point FK relations
        DeviceStatusLog.objects.filter(device=device).update(device=new_device)
        CommandLog.objects.filter(device=device).update(device=new_device)
        # Delete old device
        device.delete()


def _merge_devices(source, target):
    """Merge source device into target, resolving metric conflicts.

    For each metric present in both devices, keep the entire series from
    whichever device has the most recent reading for that metric.
    """
    with transaction.atomic():
        source_metrics = set(
            SensorReading.objects.filter(device_id=source.device_id)
            .values_list("metric", flat=True)
            .distinct()
        )
        target_metrics = set(
            SensorReading.objects.filter(device_id=target.device_id)
            .values_list("metric", flat=True)
            .distinct()
        )

        shared_metrics = source_metrics & target_metrics

        for metric in shared_metrics:
            source_latest = (
                SensorReading.objects.filter(
                    device_id=source.device_id, metric=metric
                )
                .order_by("-time")
                .values_list("time", flat=True)
                .first()
            )
            target_latest = (
                SensorReading.objects.filter(
                    device_id=target.device_id, metric=metric
                )
                .order_by("-time")
                .values_list("time", flat=True)
                .first()
            )

            if source_latest and target_latest and source_latest > target_latest:
                # Source wins: delete target's series, re-assign source's
                SensorReading.objects.filter(
                    device_id=target.device_id, metric=metric
                ).delete()
                SensorReading.objects.filter(
                    device_id=source.device_id, metric=metric
                ).update(device_id=target.device_id)
            else:
                # Target wins (or tie): delete source's series
                SensorReading.objects.filter(
                    device_id=source.device_id, metric=metric
                ).delete()

        # Move remaining source-only metrics
        SensorReading.objects.filter(device_id=source.device_id).update(
            device_id=target.device_id
        )

        # Move status logs and commands to target
        DeviceStatusLog.objects.filter(device=source).update(device=target)
        CommandLog.objects.filter(device=source).update(device=target)

        # Update target metadata: keep most recent last_seen
        if source.last_seen and (
            target.last_seen is None or source.last_seen > target.last_seen
        ):
            target.last_seen = source.last_seen
            target.save(update_fields=["last_seen"])

        # Delete source device
        source.delete()

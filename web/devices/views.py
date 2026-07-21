import json
import logging
import re

from django.db import connection, transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from accounts.decorators import role_required
from mqtt_bridge.services import (
    _mqtt_publish,
    request_calibration,
    request_capabilities,
    request_commands,
    request_diag,
    request_status,
)
from ota.models import HardwareRevision
from ota.services import OtaError, compatible_firmwares, send_ota_update
from readings.metrics import get_metrics_display_map
from readings.models import SensorReading

from .models import CommandLog, Device, DeviceStatusLog

SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# How many recent diag snapshots to surface in the per-device views.
DIAG_LOG_LIMIT = 30

logger = logging.getLogger(__name__)


def _prev_next_device(device_id):
    """Return (prev_id, next_id) wrapping around the approved device list."""
    ids = list(
        Device.objects.filter(is_approved=True)
        .order_by("device_id")
        .values_list("device_id", flat=True)
    )
    if not ids or device_id not in ids:
        return None, None
    idx = ids.index(device_id)
    return ids[idx - 1], ids[(idx + 1) % len(ids)]


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

    prev_id, next_id = _prev_next_device(device_id)

    # Diagnostics message log, admins only: the most recent diag snapshots (level
    # + message + key detail). Same source as the device admin page's technical
    # table, surfaced here so an admin reading the charts sees the health history
    # without leaving the page.
    diag_logs = (
        list(device.diag_logs.all()[:DIAG_LOG_LIMIT])
        if is_admin and device.supports_diag else []
    )

    return render(request, "devices/device_history.html", {
        "device": device,
        "metrics": metrics,
        "metrics_json": json.dumps(metrics),
        "units_json": json.dumps(units),
        "metrics_display_json": json.dumps(metrics_display),
        "is_admin": is_admin,
        "diag_logs": diag_logs,
        "prev_device_id": prev_id,
        "next_device_id": next_id,
    })


CALIBRATION_METRICS = [
    ("temp", _("Temperature"), "°C"),
    ("humi", _("Humidity"), "%"),
    ("press", _("Pressure"), "hPa"),
]


@role_required("admin")
def device_admin_view(request, device_id):
    device = get_object_or_404(Device, device_id=device_id)
    commands = device.commands.select_related("sent_by")[:20]
    command_params = (device.capabilities or {}).get("command_params", {})
    prev_id, next_id = _prev_next_device(device_id)

    # Calibration is read from the unified server-side mirror (Device.calibration).
    # Offsets are stored under cal_<metric>; the battery divider under bat_divider.
    device_commands = (device.capabilities or {}).get("commands", [])
    mirror = device.calibration or {}
    has_offset_calibration = "set_offset" in device_commands
    has_set_calibration = "set_calibration" in device_commands
    calibration_metrics = [
        {"key": key, "label": str(label), "unit": unit, "offset": mirror.get("cal_" + key, 0.0)}
        for key, label, unit in CALIBRATION_METRICS
    ] if has_offset_calibration else []

    # Battery voltage-divider calibration (set_calibration key=bat_divider), with
    # the revision's nominal value as guidance.
    bat_divider = None
    if has_set_calibration:
        nominal = None
        if device.hardware_code_id and device.hw_rev is not None:
            rev = HardwareRevision.objects.filter(
                hardware_code_id=device.hardware_code_id, hw_rev=device.hw_rev
            ).first()
            nominal = rev.bat_divider_nominal if rev else None
        bat_divider = {"value": mirror.get("bat_divider"), "nominal": nominal}

    has_calibration = has_offset_calibration or has_set_calibration

    # OTA: offer compatible firmwares only for OTA-capable devices.
    ota_firmwares = list(compatible_firmwares(device)) if device.ota_capable else []

    # Diagnostics: recent health snapshots (diag topic), diag-capable devices only.
    diag_logs = list(device.diag_logs.all()[:DIAG_LOG_LIMIT]) if device.supports_diag else []

    # Uplink-delivery confirmation toggle (set_confirm_uplink, ESP32 opt-in). The
    # current on/off state is inferred from the latest diag: the txsent/txok
    # counters ride the payload only while the mode is on.
    supports_confirm_uplink = "set_confirm_uplink" in device_commands
    confirm_uplink_on = bool(diag_logs and diag_logs[0].txsent is not None)

    return render(request, "devices/device_admin.html", {
        "device": device,
        "commands": commands,
        "command_params_json": json.dumps(command_params),
        "prev_device_id": prev_id,
        "next_device_id": next_id,
        "has_calibration": has_calibration,
        "has_offset_calibration": has_offset_calibration,
        "has_set_calibration": has_set_calibration,
        "calibration_metrics": calibration_metrics,
        "bat_divider": bat_divider,
        "calibration_mirror": mirror,
        "ota_firmwares": ota_firmwares,
        "diag_logs": diag_logs,
        "supports_confirm_uplink": supports_confirm_uplink,
        "confirm_uplink_on": confirm_uplink_on,
    })


@role_required("admin")
def device_ota_push_view(request, device_id):
    """Push a compatible firmware to a device (admin action)."""
    device = get_object_or_404(Device, device_id=device_id)
    if request.method != "POST":
        return HttpResponseBadRequest()

    firmware = compatible_firmwares(device).filter(pk=request.POST.get("firmware_id")).first()
    if firmware is None:
        return HttpResponseBadRequest(_("Unknown or incompatible firmware."))

    try:
        send_ota_update(device, firmware, sent_by=request.user)
    except OtaError as exc:
        logger.warning("ota push %s -> refused: %s", device_id, exc)
        return HttpResponseBadRequest(str(exc))
    except Exception:
        logger.exception("Failed to publish ota_update to %s", device_id)
        return HttpResponseBadRequest(_("MQTT publish error."))

    if request.headers.get("HX-Request"):
        commands = device.commands.select_related("sent_by")[:20]
        return render(request, "devices/_command_log.html", {"commands": commands, "device": device})
    return redirect("devices:admin", device_id=device.device_id)


@role_required("admin")
def device_edit_view(request, device_id):
    device = get_object_or_404(Device, device_id=device_id)
    metrics = _get_device_metrics(device)

    if request.method == "POST":
        device.display_name = request.POST.get("display_name", "")
        device.location = request.POST.get("location", "")
        device.location_type = request.POST.get("location_type", "")
        device.guest_visible_metrics = request.POST.getlist("guest_visible_metrics")
        try:
            interval = int(request.POST.get("publish_interval", 0))
            device.publish_interval = max(0, min(interval, 86400))
        except (ValueError, TypeError):
            pass
        try:
            cell_count = int(request.POST.get("battery_cell_count", 1))
            device.battery_cell_count = max(1, min(cell_count, 10))
        except (ValueError, TypeError):
            pass
        device.save(update_fields=["display_name", "location", "location_type", "guest_visible_metrics", "publish_interval", "battery_cell_count"])
        if request.headers.get("HX-Request"):
            return render(request, "devices/_device_card.html", {"device": device})
        return redirect("devices:admin", device_id=device.device_id)

    return render(request, "devices/device_edit.html", {
        "device": device,
        "metrics": metrics,
    })


def _publish_calibration_command(device, command_data, user):
    """Publish a calibration command and mirror the value server-side.

    retain=False, qos=1: queued for deep-sleep devices via the persistent
    session, never left retained. Logged as a CommandLog. The mirror
    (Device.calibration) is updated so the server can re-push after a store wipe.
    """
    topic = f"{device.device_type}/{device.device_id}/command"
    _mqtt_publish(topic, json.dumps(command_data), retain=False, qos=1)
    CommandLog.objects.create(device=device, command=command_data, sent_by=user)


@role_required("admin")
def device_calibration_view(request, device_id):
    """Set a sensor offset (set_offset) for a metric and mirror it."""
    device = get_object_or_404(Device, device_id=device_id)
    if request.method != "POST":
        return HttpResponseBadRequest()

    metric = request.POST.get("metric", "").strip()
    valid_keys = [k for k, _, _ in CALIBRATION_METRICS]
    if metric not in valid_keys:
        return HttpResponseBadRequest(_("Invalid metric."))

    try:
        value = round(float(request.POST.get("value", "0")), 2)
    except (ValueError, TypeError):
        return HttpResponseBadRequest(_("Invalid value."))

    command_data = {"action": "set_offset", "metric": metric, "value": value}
    try:
        _publish_calibration_command(device, command_data, request.user)
    except Exception:
        logger.exception("Failed to publish calibration command to %s", device.device_id)
        return HttpResponseBadRequest(_("MQTT publish error."))

    # Update the unified mirror (offsets stored under cal_<metric>).
    mirror = device.calibration or {}
    mirror["cal_" + metric] = value
    device.calibration = mirror
    device.save(update_fields=["calibration"])

    if request.headers.get("HX-Request"):
        return render(request, "devices/_calibration_status.html", {
            "metric": metric,
            "value": value,
        })
    return redirect("devices:admin", device_id=device.device_id)


@role_required("admin")
def device_set_calibration_view(request, device_id):
    """Set a generic calibration value (set_calibration), e.g. bat_divider."""
    device = get_object_or_404(Device, device_id=device_id)
    if request.method != "POST":
        return HttpResponseBadRequest()

    key = request.POST.get("key", "").strip()
    if not SAFE_IDENTIFIER_RE.match(key) or len(key) > 64:
        return HttpResponseBadRequest(_("Invalid calibration key."))

    try:
        value = round(float(request.POST.get("value", "0")), 4)
    except (ValueError, TypeError):
        return HttpResponseBadRequest(_("Invalid value."))

    command_data = {"action": "set_calibration", "key": key, "value": value}
    try:
        _publish_calibration_command(device, command_data, request.user)
    except Exception:
        logger.exception("Failed to publish set_calibration to %s", device.device_id)
        return HttpResponseBadRequest(_("MQTT publish error."))

    mirror = device.calibration or {}
    mirror[key] = value
    device.calibration = mirror
    device.save(update_fields=["calibration"])

    if request.headers.get("HX-Request"):
        return render(request, "devices/_calibration_status.html", {
            "metric": key,
            "value": value,
        })
    return redirect("devices:admin", device_id=device.device_id)


@role_required("admin")
def device_request_calibration_view(request, device_id):
    """Ask the device to report its current calibration (one-shot request)."""
    device = get_object_or_404(Device, device_id=device_id)
    if request.method != "POST":
        return HttpResponseBadRequest()

    request_calibration(device)

    if request.headers.get("HX-Request"):
        commands = device.commands.select_related("sent_by")[:20]
        return render(request, "devices/_command_log.html", {"commands": commands, "device": device})
    return redirect("devices:admin", device_id=device.device_id)


@role_required("admin")
def device_request_status_view(request, device_id):
    """Pull the device's current health state on demand (get_status).

    Gated on supports_diag: only diag-capable firmware advertises get_status.
    """
    device = get_object_or_404(Device, device_id=device_id)
    if request.method != "POST":
        return HttpResponseBadRequest()
    if not device.supports_diag:
        return HttpResponseBadRequest(_("Device does not support diagnostics."))

    request_status(device)

    if request.headers.get("HX-Request"):
        commands = device.commands.select_related("sent_by")[:20]
        return render(request, "devices/_command_log.html", {"commands": commands, "device": device})
    return redirect("devices:admin", device_id=device.device_id)


@role_required("admin")
def device_request_diag_view(request, device_id):
    """Ask the device for a diagnostics snapshot on demand (get_diag).

    Gated on supports_diag: only diag-capable firmware advertises get_diag.
    """
    device = get_object_or_404(Device, device_id=device_id)
    if request.method != "POST":
        return HttpResponseBadRequest()
    if not device.supports_diag:
        return HttpResponseBadRequest(_("Device does not support diagnostics."))

    request_diag(device)

    if request.headers.get("HX-Request"):
        commands = device.commands.select_related("sent_by")[:20]
        return render(request, "devices/_command_log.html", {"commands": commands, "device": device})
    return redirect("devices:admin", device_id=device.device_id)


@role_required("admin")
def device_set_confirm_uplink_view(request, device_id):
    """Toggle the device's uplink-delivery confirmation diagnostic (set_confirm_uplink).

    ESP32-only opt-in diagnostic: while on, the device confirms each publish via
    broker loopback and reports txsent/txok counters in its diag payload (and
    publishes diag every wake). Gated on the command being advertised. Logged as
    a pending command; the device acks it, resolving the log entry.
    """
    device = get_object_or_404(Device, device_id=device_id)
    if request.method != "POST":
        return HttpResponseBadRequest()
    if "set_confirm_uplink" not in (device.capabilities or {}).get("commands", []):
        return HttpResponseBadRequest(_("Device does not support uplink confirmation."))

    value = 1 if request.POST.get("value") == "1" else 0
    command_data = {"action": "set_confirm_uplink", "value": value}
    topic = f"{device.device_type}/{device.device_id}/command"
    try:
        # retain=False, qos=1: queued for deep-sleep devices via the persistent
        # session; never retained (a retained command re-fires on every reconnect).
        _mqtt_publish(topic, json.dumps(command_data), retain=False, qos=1)
    except Exception:
        logger.exception("Failed to publish MQTT command to %s", topic)
        return HttpResponseBadRequest(_("MQTT publish error."))

    CommandLog.objects.create(
        device=device,
        command=command_data,
        sent_by=request.user,
        status=CommandLog.STATUS_PENDING,
    )
    logger.info("set_confirm_uplink %s/%s -> value=%d", device.device_type, device.device_id, value)

    if request.headers.get("HX-Request"):
        commands = device.commands.select_related("sent_by")[:20]
        return render(request, "devices/_command_log.html", {"commands": commands, "device": device})
    return redirect("devices:admin", device_id=device.device_id)


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
        # retain=False, qos=1: queued for deep-sleep devices via the persistent
        # session; never retained (a retained command re-fires on every reconnect).
        _mqtt_publish(topic, payload, retain=False, qos=1)
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

    # Logged as a pending command; resolved to success/timeout when the device
    # responds on the capabilities topic (or the capabilities timeout fires).
    request_capabilities(device, sent_by=request.user, log_command=True)
    # The command list is a separate message (512-byte packet limit); request it
    # too so the admin sees the full, up-to-date capabilities.
    request_commands(device)

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


def _refresh_continuous_aggregates():
    """Refresh TimescaleDB continuous aggregates after modifying raw readings.

    Charts use readings_hourly (>48h) and readings_daily (>90d) which are
    materialized views.  After changing device_id in the raw table, the
    aggregates must be rebuilt so they reflect the new identifiers.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "CALL refresh_continuous_aggregate('readings_hourly', NULL, NULL);"
        )
        cursor.execute(
            "CALL refresh_continuous_aggregate('readings_daily', NULL, NULL);"
        )


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
            hardware_code=device.hardware_code,
            hw_rev=device.hw_rev,
            ota_capable=device.ota_capable,
            calibration=device.calibration,
            fw_version=device.fw_version,
            battery_percent=device.battery_percent,
            capabilities=device.capabilities,
            publish_interval=device.publish_interval,
            alert_level=device.alert_level,
            alert_message=device.alert_message,
            alert_updated_at=device.alert_updated_at,
            diag_requested_at=device.diag_requested_at,
            capabilities_requested_at=device.capabilities_requested_at,
            guest_visible_metrics=device.guest_visible_metrics,
            battery_cell_count=device.battery_cell_count,
        )
        new_device = Device.objects.get(device_id=new_id)
        # Re-point FK relations
        DeviceStatusLog.objects.filter(device=device).update(device=new_device)
        CommandLog.objects.filter(device=device).update(device=new_device)
        # Delete old device
        device.delete()
    _refresh_continuous_aggregates()


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
    _refresh_continuous_aggregates()

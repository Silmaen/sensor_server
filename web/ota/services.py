"""OTA push orchestration (server → device).

Publishes the ``ota_update`` command for a compatible firmware and records it as
a pending CommandLog. Success is not acked (the device reboots); it is detected
when the device next reports capabilities carrying the pushed version (see
mqtt_bridge.services). An ``error`` ack marks the command failed. See
docs/ota-server.md §6.
"""

import json
import logging

from django.conf import settings

from devices.models import CommandLog
from mqtt_bridge.services import _mqtt_publish

logger = logging.getLogger(__name__)


class OtaError(Exception):
    """Raised when an OTA push cannot be initiated (guard failure)."""


def firmware_download_url(firmware):
    """Device-facing download URL, per the frozen contract: ``/fw/<hw>/<rev>/<version>.bin``.

    Built from ``OTA_BASE_URL`` (LAN-reachable), not the Django media URL: nginx
    exposes the firmware media directory at ``/fw/``.
    """
    rev = firmware.hardware_revision
    return f"{settings.OTA_BASE_URL}/fw/{rev.hardware_code_id}/{rev.hw_rev}/{firmware.version}.bin"


def send_ota_update(device, firmware, sent_by=None):
    """Publish an ``ota_update`` command to *device* for *firmware*.

    Guards (raise :class:`OtaError`): the device must be OTA-capable and its
    ``(hardware_code, hw_rev)`` must match the firmware's — the server never
    pushes an image of another code/revision. Published QoS 1, retain=false
    (queued for a deep-sleep device via its persistent session; retain=false
    avoids redelivery after the reboot, the firmware's ``ver`` guard being the
    backstop). Returns the pending CommandLog.
    """
    rev = firmware.hardware_revision
    if not device.ota_capable:
        raise OtaError("Device is not OTA-capable.")
    if device.hardware_code_id != rev.hardware_code_id or device.hw_rev != rev.hw_rev:
        raise OtaError("Firmware is not compatible with the device (hw_code/hw_rev mismatch).")
    if not settings.OTA_BASE_URL:
        raise OtaError("OTA_BASE_URL is not configured.")

    command = {
        "action": "ota_update",
        "value": firmware_download_url(firmware),
        "md5": firmware.md5,
        "ver": firmware.version,
        "hw": rev.hardware_code_id,
        "hwrev": rev.hw_rev,
    }
    topic = f"{device.device_type}/{device.device_id}/command"
    _mqtt_publish(topic, json.dumps(command), retain=False, qos=1)
    log = CommandLog.objects.create(device=device, command=command, sent_by=sent_by)
    logger.info(
        "ota_update %s -> %s rev%s v%s (md5=%s)",
        device.device_id, rev.hardware_code_id, rev.hw_rev, firmware.version, firmware.md5,
    )
    return log


def compatible_firmwares(device):
    """Firmwares that may be pushed to *device* (matching hw_code + hw_rev).

    Empty unless the device resolved a registry hardware code and reported a
    revision. Callers should still gate on ``device.ota_capable`` for the UI.
    """
    from .models import Firmware

    if device.hardware_code_id is None or device.hw_rev is None:
        return Firmware.objects.none()
    return (
        Firmware.objects
        .select_related("hardware_revision__hardware_code")
        .filter(
            hardware_revision__hardware_code_id=device.hardware_code_id,
            hardware_revision__hw_rev=device.hw_rev,
        )
        .order_by("-uploaded_at")
    )

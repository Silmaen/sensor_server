"""Firmware publication API (internal, CI-facing).

Write endpoints called by the firmware project's ``publish_firmware.py`` to keep
the hardware registry and firmware catalog in sync and to upload images. All
writes are idempotent and authenticated with the ``OTA_PUBLISH_TOKEN`` Bearer
token (see ``ota.auth``), which is separate from the read-only ``api.ApiKey``.

Endpoints (see docs/ota-server.md §3):
  A1  PUT  /api/hw/codes/<hw_code>
  A2  PUT  /api/hw/codes/<hw_code>/revs/<hw_rev>
  A3  POST /api/firmwares
  A4  GET  /api/firmwares
  A5  GET  /api/firmwares/latest
"""

import hashlib
import json
import logging
import re

from django.core.files.base import File
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from accounts.decorators import role_required

from .auth import publish_token_required
from .models import Firmware, HardwareCode, HardwareRevision

logger = logging.getLogger(__name__)

HW_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9.\-+]{1,32}$")
MAX_FIRMWARE_SIZE = 8 * 1024 * 1024  # 8 MB — generous ceiling for an ESP image.


def _bad_request(detail):
    return JsonResponse({"error": "bad_request", "detail": detail}, status=400)


def _not_found(detail):
    return JsonResponse({"error": "not_found", "detail": detail}, status=404)


def _hw_code_dict(hc):
    return {
        "hw_code": hc.hw_code,
        "platform": hc.platform,
        "description": hc.description,
        "modules": hc.modules,
    }


def _revision_dict(rev):
    return {
        "hw_code": rev.hardware_code_id,
        "hw_rev": rev.hw_rev,
        "description": rev.description,
        "bat_divider_nominal": rev.bat_divider_nominal,
        "notes": rev.notes,
    }


def _firmware_dict(fw):
    return {
        "hw_code": fw.hardware_revision.hardware_code_id,
        "hw_rev": fw.hardware_revision.hw_rev,
        "version": fw.version,
        "md5": fw.md5,
        "size": fw.size,
        "url": fw.file.url if fw.file else None,
        "uploaded_at": fw.uploaded_at.isoformat(),
        "notes": fw.notes,
    }


def _parse_json_body(request):
    try:
        data = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _md5_of(django_file):
    """Stream the uploaded file through MD5 without loading it into memory."""
    h = hashlib.md5()
    for chunk in django_file.chunks():
        h.update(chunk)
    django_file.seek(0)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# A1 — PUT /api/hw/codes/<hw_code>
# ---------------------------------------------------------------------------

@csrf_exempt
@publish_token_required
def hw_code_view(request, hw_code):
    if request.method != "PUT":
        return _bad_request("Use PUT to upsert a hardware code.")
    if not HW_CODE_RE.match(hw_code):
        return _bad_request("hw_code must match ^[A-Z0-9]{8}$.")

    data = _parse_json_body(request)
    if data is None:
        return _bad_request("Body must be a JSON object.")

    platform = data.get("platform", "")
    description = data.get("description", "")
    modules = data.get("modules", [])
    if not isinstance(platform, str) or not isinstance(description, str):
        return _bad_request("'platform' and 'description' must be strings.")
    if not isinstance(modules, list) or not all(isinstance(m, str) for m in modules):
        return _bad_request("'modules' must be a list of strings.")

    hc, created = HardwareCode.objects.update_or_create(
        hw_code=hw_code,
        defaults={"platform": platform[:32], "description": description[:256], "modules": modules},
    )
    logger.info("publish A1 -> hw_code %s %s", hw_code, "created" if created else "updated")
    return JsonResponse(_hw_code_dict(hc), status=201 if created else 200)


# ---------------------------------------------------------------------------
# A2 — PUT /api/hw/codes/<hw_code>/revs/<hw_rev>
# ---------------------------------------------------------------------------

@csrf_exempt
@publish_token_required
def hw_revision_view(request, hw_code, hw_rev):
    if request.method != "PUT":
        return _bad_request("Use PUT to upsert a hardware revision.")
    if not HW_CODE_RE.match(hw_code):
        return _bad_request("hw_code must match ^[A-Z0-9]{8}$.")

    try:
        hc = HardwareCode.objects.get(pk=hw_code)
    except HardwareCode.DoesNotExist:
        return _not_found("Unknown hw_code; register it first (A1).")

    data = _parse_json_body(request)
    if data is None:
        return _bad_request("Body must be a JSON object.")

    description = data.get("description", "")
    notes = data.get("notes", "")
    bat_divider = data.get("bat_divider_nominal")
    if not isinstance(description, str) or not isinstance(notes, str):
        return _bad_request("'description' and 'notes' must be strings.")
    if bat_divider is not None and not isinstance(bat_divider, (int, float)):
        return _bad_request("'bat_divider_nominal' must be a number.")

    rev, created = HardwareRevision.objects.update_or_create(
        hardware_code=hc,
        hw_rev=hw_rev,
        defaults={
            "description": description[:256],
            "notes": notes,
            "bat_divider_nominal": bat_divider,
        },
    )
    logger.info("publish A2 -> %s rev%s %s", hw_code, hw_rev, "created" if created else "updated")
    return JsonResponse(_revision_dict(rev), status=201 if created else 200)


# ---------------------------------------------------------------------------
# A3 — POST /api/firmwares   |   A4 — GET /api/firmwares
# ---------------------------------------------------------------------------

@csrf_exempt
@publish_token_required
def firmwares_view(request):
    if request.method == "GET":
        return _list_firmwares(request)
    if request.method == "POST":
        return _publish_firmware(request)
    return _bad_request("Use GET to list or POST to publish.")


def _list_firmwares(request):
    qs = Firmware.objects.select_related("hardware_revision__hardware_code")
    hw_code = request.GET.get("hw_code")
    hw_rev = request.GET.get("hw_rev")
    if hw_code:
        qs = qs.filter(hardware_revision__hardware_code_id=hw_code)
    if hw_rev:
        try:
            qs = qs.filter(hardware_revision__hw_rev=int(hw_rev))
        except ValueError:
            return _bad_request("'hw_rev' must be an integer.")
    firmwares = [_firmware_dict(fw) for fw in qs]
    return JsonResponse({"count": len(firmwares), "firmwares": firmwares})


def _publish_firmware(request):
    hw_code = request.POST.get("hw_code", "")
    version = request.POST.get("version", "")
    md5 = request.POST.get("md5", "").lower()
    notes = request.POST.get("notes", "")
    upload = request.FILES.get("firmware.bin") or request.FILES.get("file")

    if not HW_CODE_RE.match(hw_code):
        return _bad_request("hw_code must match ^[A-Z0-9]{8}$.")
    if not VERSION_RE.match(version):
        return _bad_request("Invalid 'version'.")
    try:
        hw_rev = int(request.POST.get("hw_rev", ""))
    except ValueError:
        return _bad_request("'hw_rev' must be an integer.")
    if not re.fullmatch(r"[0-9a-f]{32}", md5):
        return _bad_request("'md5' must be a 32-char hex digest.")
    if upload is None:
        return _bad_request("Missing firmware file (field 'firmware.bin').")
    if upload.size > MAX_FIRMWARE_SIZE:
        return _bad_request("Firmware exceeds the maximum size.")

    try:
        rev = HardwareRevision.objects.get(hardware_code_id=hw_code, hw_rev=hw_rev)
    except HardwareRevision.DoesNotExist:
        return _bad_request("Unknown (hw_code, hw_rev); register them first (A1/A2).")

    # Recompute the MD5 server-side (end-to-end integrity up to hosting).
    actual_md5 = _md5_of(upload)
    if actual_md5 != md5:
        return _bad_request(f"MD5 mismatch: announced {md5}, computed {actual_md5}.")

    overwrite = request.GET.get("overwrite") == "true"
    existing = Firmware.objects.filter(hardware_revision=rev, version=version).first()
    if existing and not overwrite:
        return JsonResponse(
            {"error": "conflict",
             "detail": "This (hw_code, hw_rev, version) already exists. Use ?overwrite=true to replace."},
            status=409,
        )

    with transaction.atomic():
        fw = existing or Firmware(hardware_revision=rev, version=version)
        if existing and existing.file:
            existing.file.delete(save=False)  # replace the stored binary
        fw.md5 = actual_md5
        fw.size = upload.size
        fw.notes = notes
        fw.file.save(f"{version}.bin", File(upload), save=False)
        fw.save()

    logger.info("publish A3 -> firmware %s rev%s v%s (%d bytes, %s)",
                hw_code, hw_rev, version, fw.size, "replaced" if existing else "created")
    return JsonResponse(_firmware_dict(fw), status=200 if existing else 201)


# ---------------------------------------------------------------------------
# A5 — GET /api/firmwares/latest
# ---------------------------------------------------------------------------

@csrf_exempt
@publish_token_required
def firmware_latest_view(request):
    if request.method != "GET":
        return _bad_request("Use GET.")
    hw_code = request.GET.get("hw_code")
    hw_rev = request.GET.get("hw_rev")
    if not hw_code or not hw_rev:
        return _bad_request("'hw_code' and 'hw_rev' are required.")
    try:
        hw_rev_int = int(hw_rev)
    except ValueError:
        return _bad_request("'hw_rev' must be an integer.")

    # "Latest" = most recently published (uploaded_at), not semver-sorted.
    fw = (
        Firmware.objects
        .select_related("hardware_revision__hardware_code")
        .filter(hardware_revision__hardware_code_id=hw_code, hardware_revision__hw_rev=hw_rev_int)
        .order_by("-uploaded_at")
        .first()
    )
    if fw is None:
        return _not_found("No firmware published for this (hw_code, hw_rev).")
    return JsonResponse(_firmware_dict(fw))


# ---------------------------------------------------------------------------
# Human-facing overview page (site UI, not the CI API)
# ---------------------------------------------------------------------------

@role_required("admin")
def firmware_overview_view(request):
    """Admin page listing published firmwares grouped by hardware code/revision.

    Read-only counterpart to the JSON publication API: shows every image with
    version, size, MD5, upload date and a download link, and marks the latest
    per revision (most recently uploaded). Also surfaces how many real devices
    resolve to each hardware code.
    """
    codes = (
        HardwareCode.objects
        .prefetch_related("revisions__firmwares", "devices")
        .all()
    )
    groups = []
    for code in codes:
        revisions = []
        for rev in code.revisions.all():
            firmwares = sorted(
                rev.firmwares.all(), key=lambda f: f.uploaded_at, reverse=True
            )
            latest_id = firmwares[0].id if firmwares else None
            revisions.append(
                {"rev": rev, "firmwares": firmwares, "latest_id": latest_id}
            )
        groups.append(
            {
                "code": code,
                "revisions": revisions,
                # len() over the prefetched cache — no extra COUNT query.
                "device_count": len(code.devices.all()),
                "firmware_count": sum(len(r["firmwares"]) for r in revisions),
            }
        )
    return render(request, "ota/firmware_overview.html", {"groups": groups})

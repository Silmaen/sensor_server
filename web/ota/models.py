"""Hardware registry and firmware catalog for OTA delivery.

These tables are the source of truth for which hardware types/revisions exist
and which firmware images have been published for them. They are populated
**only** by the publication API (never from device reports): a device that
claims a code absent from this registry is treated as running un-published
firmware. See docs/ota-server.md.
"""

from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


# An 8-character fixed-length code: <FF> platform family + <TTTTTT> type token.
HW_CODE_VALIDATOR = RegexValidator(
    r"^[A-Z0-9]{8}$",
    message=_("Hardware code must be exactly 8 uppercase alphanumeric characters."),
)


class HardwareCode(models.Model):
    """A functional hardware type (sensor/module set), keyed by an 8-char code.

    The code itself is just a stable key; the descriptive meaning lives here.
    """

    hw_code = models.CharField(
        max_length=8, primary_key=True, validators=[HW_CODE_VALIDATOR]
    )
    platform = models.CharField(max_length=32, blank=True, default="")
    description = models.CharField(max_length=256, blank=True, default="")
    modules = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["hw_code"]
        verbose_name = _("hardware code")
        verbose_name_plural = _("hardware codes")

    def __str__(self):
        return self.hw_code


class HardwareRevision(models.Model):
    """A physical/electrical revision of a hardware code.

    Absorbs PCB/wiring/electronics changes that alter the binary. An image is
    valid for one ``(hw_code, hw_rev)`` couple only.
    """

    hardware_code = models.ForeignKey(
        HardwareCode, on_delete=models.CASCADE, related_name="revisions"
    )
    hw_rev = models.PositiveSmallIntegerField()
    description = models.CharField(max_length=256, blank=True, default="")
    # Nominal voltage-divider ratio for this revision; the fine per-unit value
    # is calibration (mirrored on the Device), not a revision constant.
    bat_divider_nominal = models.FloatField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["hardware_code", "hw_rev"]
        verbose_name = _("hardware revision")
        verbose_name_plural = _("hardware revisions")
        constraints = [
            models.UniqueConstraint(
                fields=["hardware_code", "hw_rev"], name="unique_hw_code_rev"
            )
        ]

    def __str__(self):
        return f"{self.hardware_code_id} rev{self.hw_rev}"


def firmware_upload_path(instance, filename):
    """Device-agnostic hosting layout: ``fw/<hw_code>/<hw_rev>/<version>.bin``."""
    rev = instance.hardware_revision
    return f"fw/{rev.hardware_code_id}/{rev.hw_rev}/{instance.version}.bin"


class Firmware(models.Model):
    """A published firmware image, identified by ``(hw_code, hw_rev, version)``."""

    hardware_revision = models.ForeignKey(
        HardwareRevision, on_delete=models.PROTECT, related_name="firmwares"
    )
    version = models.CharField(max_length=32)
    file = models.FileField(upload_to=firmware_upload_path)
    # MD5 is recomputed server-side on upload and rejected on mismatch.
    md5 = models.CharField(max_length=32)
    size = models.PositiveIntegerField()
    uploaded_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["hardware_revision", "-uploaded_at"]
        verbose_name = _("firmware")
        verbose_name_plural = _("firmwares")
        constraints = [
            models.UniqueConstraint(
                fields=["hardware_revision", "version"],
                name="unique_firmware_rev_version",
            )
        ]

    def __str__(self):
        return f"{self.hardware_revision} v{self.version}"

    @property
    def hw_code(self):
        return self.hardware_revision.hardware_code_id

    @property
    def hw_rev(self):
        return self.hardware_revision.hw_rev

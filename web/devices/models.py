from django.conf import settings
from django.db import models
from django.utils import timezone


ALERT_LEVEL_CHOICES = [
    ("", "OK"),
    ("warning", "Warning"),
    ("error", "Error"),
]

LOCATION_TYPE_CHOICES = [
    ("", "—"),
    ("indoor", "Indoor"),
    ("outdoor", "Outdoor"),
]

# Default timeout when publish_interval is not yet known (5 minutes).
DEFAULT_OFFLINE_TIMEOUT = 300

# Time to wait for a capabilities response before flagging an error (seconds).
CAPABILITIES_RESPONSE_TIMEOUT = 60

# Server-side battery state-of-charge thresholds (percent) used to raise
# low-battery alerts from the latest reported bat_percent, independently of
# the device firmware's own status messages. Chosen to cover both platforms
# (ESP 2S warns at 15%, MKR 1S at 20%).
LOW_BATTERY_THRESHOLD = 20
CRITICAL_BATTERY_THRESHOLD = 5


class Device(models.Model):
    device_id = models.CharField(max_length=128, primary_key=True)
    device_type = models.CharField(max_length=64, default="unknown")
    display_name = models.CharField(max_length=128, blank=True, default="")
    location = models.CharField(max_length=128, blank=True, default="")
    config = models.JSONField(default=dict, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_approved = models.BooleanField(default=False)
    hardware_id = models.CharField(max_length=256, blank=True, default="")
    # Hardware code (shared across identical hardware) and firmware version,
    # reported in the capabilities message. Absence signals outdated firmware.
    hw_code = models.CharField(max_length=64, blank=True, default="")
    fw_version = models.CharField(max_length=32, blank=True, default="")
    # Latest reported battery state of charge (percent), for low-battery alerts.
    battery_percent = models.FloatField(null=True, blank=True)
    capabilities = models.JSONField(default=dict, blank=True)
    publish_interval = models.PositiveIntegerField(default=0)
    alert_level = models.CharField(
        max_length=16, blank=True, default="", choices=ALERT_LEVEL_CHOICES
    )
    alert_message = models.CharField(max_length=256, blank=True, default="")
    capabilities_requested_at = models.DateTimeField(null=True, blank=True)
    guest_visible_metrics = models.JSONField(default=list, blank=True)
    location_type = models.CharField(
        max_length=16, blank=True, default="", choices=LOCATION_TYPE_CHOICES
    )
    battery_cell_count = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ["device_id"]

    def __str__(self):
        return self.display_name or self.device_id

    @property
    def effective_name(self):
        return self.display_name or self.device_id

    @property
    def is_online(self):
        if self.last_seen is None:
            return False
        timeout = (
            self.publish_interval * 3
            if self.publish_interval > 0
            else DEFAULT_OFFLINE_TIMEOUT
        )
        return (timezone.now() - self.last_seen).total_seconds() < timeout

    @property
    def has_reported_capabilities(self):
        """True once the device has answered a capabilities request."""
        return bool(self.capabilities and self.capabilities.get("metrics"))

    @property
    def battery_status(self):
        """Battery health from the latest bat_percent reading.

        Returns "critical", "low", "ok", or None when no battery reading is
        known (e.g. mains-powered devices).
        """
        if self.battery_percent is None:
            return None
        if self.battery_percent <= CRITICAL_BATTERY_THRESHOLD:
            return "critical"
        if self.battery_percent <= LOW_BATTERY_THRESHOLD:
            return "low"
        return "ok"

    @property
    def is_battery_low(self):
        return self.battery_status in ("low", "critical")

    @property
    def needs_firmware_update(self):
        """True when the device runs firmware too old to advertise hw/fw.

        A device that has answered a capabilities request but reports neither a
        hardware code nor a firmware version is assumed to run outdated
        firmware, so an update is recommended.
        """
        if not self.has_reported_capabilities:
            return False
        return not self.hw_code or not self.fw_version


class DeviceStatusLog(models.Model):
    time = models.DateTimeField()
    device = models.ForeignKey(
        Device, on_delete=models.CASCADE, related_name="status_logs"
    )
    alert_level = models.CharField(
        max_length=16, blank=True, default="", choices=ALERT_LEVEL_CHOICES
    )
    alert_message = models.CharField(max_length=256, blank=True, default="")

    class Meta:
        ordering = ["-time"]
        indexes = [models.Index(fields=["device", "time"])]

    def __str__(self):
        return f"{self.device_id} {self.alert_level or 'ok'} @ {self.time:%Y-%m-%d %H:%M}"


class CommandLog(models.Model):
    device = models.ForeignKey(
        Device, on_delete=models.CASCADE, related_name="commands"
    )
    command = models.JSONField()
    sent_at = models.DateTimeField(auto_now_add=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    acked = models.BooleanField(default=False)
    acked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-sent_at"]

    def __str__(self):
        return f"{self.device_id} @ {self.sent_at:%Y-%m-%d %H:%M}"

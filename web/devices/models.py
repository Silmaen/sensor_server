from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


ALERT_LEVEL_CHOICES = [
    ("", "OK"),
    ("warning", "Warning"),
    ("error", "Error"),
]

# Health ladder reported on the diagnostics (`diag`) topic. Wider than
# ALERT_LEVEL_CHOICES: it also carries "ok" and "info" (nominal / informational
# states that do not raise a device alert). See docs/diagnostics.md (firmware).
DIAG_LEVEL_CHOICES = [
    ("ok", "OK"),
    ("info", "Info"),
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
    # Resolved hardware type: FK to the CI-fed registry, set only when the code
    # reported in capabilities exists in it. NULL (unknown/unpublished code, or
    # no code at all) means the device is flagged for a firmware update. The raw
    # claimed code is not retained (see docs/ota-server.md §1.4).
    hardware_code = models.ForeignKey(
        "ota.HardwareCode",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="devices",
    )
    # Hardware revision (physical/electrical) reported in capabilities. Kept as
    # a plain int (no native composite FK to HardwareRevision).
    hw_rev = models.PositiveSmallIntegerField(null=True, blank=True)
    # Whether the device advertises OTA support (capabilities `ota`). The push
    # UI is offered only for OTA-capable devices. The ota_update command is
    # inferred from this flag, not the advertised command list.
    ota_capable = models.BooleanField(default=False)
    # Whether the device runs the always-on diagnostics layer (capabilities
    # `diag`). The diag commands (get_status/get_diag/set_confirm_uplink) are
    # inferred from this flag, not the advertised command list. Surfaced as
    # `supports_diag`.
    diag_capable = models.BooleanField(default=False)
    # Server-side calibration mirror (per device_id), admin-editable. Re-pushed
    # to the device after a store wipe (capabilities `cal: 0`).
    calibration = models.JSONField(default=dict, blank=True)
    fw_version = models.CharField(max_length=32, blank=True, default="")
    # Latest reported battery state of charge (percent), for low-battery alerts.
    battery_percent = models.FloatField(null=True, blank=True)
    capabilities = models.JSONField(default=dict, blank=True)
    publish_interval = models.PositiveIntegerField(default=0)
    alert_level = models.CharField(
        max_length=16, blank=True, default="", choices=ALERT_LEVEL_CHOICES
    )
    alert_message = models.CharField(max_length=256, blank=True, default="")
    # When the current alert was last asserted/re-published by the device. A live
    # warning/error is re-asserted every wake cycle; the ingestion service clears
    # an alert that has not been refreshed within the offline-detection window
    # (see mqtt_bridge.services). NULL when no alert is latched.
    alert_updated_at = models.DateTimeField(null=True, blank=True)
    # When the server last sent an automatic get_diag poll to a flagged device,
    # used to rate-limit the polling (see mqtt_bridge.services). NULL when never
    # polled or reset.
    diag_requested_at = models.DateTimeField(null=True, blank=True)
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
        """True when the device runs firmware too old or un-published.

        A device that has answered a capabilities request but whose hardware
        code does not resolve in the CI-fed registry (unknown or absent), or
        that reports no firmware version, is flagged for a firmware update.
        These causes are not distinguished (single generic state).
        """
        if not self.has_reported_capabilities:
            return False
        return self.hardware_code_id is None or not self.fw_version

    @property
    def is_legacy_firmware(self):
        """True for a device running pre-OTA firmware.

        It has answered a capabilities request but advertises neither OTA support
        nor a hardware code known to the registry — firmware from before the OTA
        rollout, which must be reflashed manually. Distinct from a device merely
        awaiting a published image (which does advertise OTA capability).
        """
        if not self.has_reported_capabilities:
            return False
        return not self.ota_capable and self.hardware_code_id is None

    @property
    def supports_diag(self):
        """True when the device runs the always-on diagnostics layer.

        Detected from the ``diag`` capability flag (``"diag":1``), not the command
        list: diagnostics-capable firmware hardcodes that flag and does NOT list
        its core diag commands (``get_status``/``get_diag``/``set_confirm_uplink``)
        in ``commands`` — the server infers them from the flag (see the firmware
        docs/diagnostics.md). Gating on this keeps the server from sending those
        commands to a node that cannot answer them.
        """
        return self.diag_capable


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


class DeviceDiagLog(models.Model):
    """A diagnostics snapshot pushed by a device on the ``diag`` topic.

    The firmware publishes ``diag`` only when its health level is at least
    ``warning``, or on demand in response to ``get_diag`` (see
    docs/diagnostics.md in the firmware repo). That makes it low-volume, so a
    plain managed table (not a TimescaleDB hypertable) is enough — it mirrors
    DeviceStatusLog. The technical fields feed the device health view; the
    ``level``/``message`` are also reflected onto ``Device.alert_*`` by the diag
    handler (a warning/error latches an alert, like a status message).

    All technical fields are nullable: they are platform-specific (e.g. SAMD21
    has no heap metric) or may be absent from a given firmware's payload.
    """
    time = models.DateTimeField()
    device = models.ForeignKey(
        Device, on_delete=models.CASCADE, related_name="diag_logs"
    )
    level = models.CharField(
        max_length=16, blank=True, default="ok", choices=DIAG_LEVEL_CHOICES
    )
    message = models.CharField(max_length=256, blank=True, default="")
    # reset_cause: 0=unknown 1=power-on 2=ext 3=sw 4=deep-sleep 5=brownout 6=panic 7=wdt
    reset_cause = models.PositiveSmallIntegerField(null=True, blank=True)
    boot = models.PositiveIntegerField(null=True, blank=True)  # total boots since cold start
    miss = models.PositiveIntegerField(null=True, blank=True)  # consecutive connect failures
    wake_ms = models.PositiveIntegerField(null=True, blank=True)  # wake -> publish duration
    seq = models.PositiveIntegerField(null=True, blank=True)  # monotonic publish counter
    pubfail = models.PositiveIntegerField(null=True, blank=True)  # publish() failures
    rssi = models.IntegerField(null=True, blank=True)  # dBm at connect
    heap = models.PositiveIntegerField(null=True, blank=True)  # free heap bytes
    battery_percent = models.PositiveSmallIntegerField(null=True, blank=True)  # bat soc
    # Uplink-delivery confirmation counters (opt-in via set_confirm_uplink): only
    # present while that mode is on, absent from a normal diag payload. txok/txsent
    # is the confirmed end-to-end delivery rate (broker loopback). See the firmware
    # docs/diagnostics.md "Uplink-delivery confirmation".
    txsent = models.PositiveIntegerField(null=True, blank=True)  # publishes attempted
    txok = models.PositiveIntegerField(null=True, blank=True)  # of those, confirmed delivered

    class Meta:
        ordering = ["-time"]
        indexes = [models.Index(fields=["device", "time"])]

    def __str__(self):
        return f"{self.device_id} diag {self.level} @ {self.time:%Y-%m-%d %H:%M}"

    @property
    def uplink_confirm_rate(self):
        """Confirmed end-to-end delivery rate (percent) for this snapshot.

        txok / txsent, present only while the uplink-confirm diagnostic is on
        (both counters reported). Returns None otherwise. Cumulative counters, so
        this is the lifetime rate; the latest attempt may lag by one wake (see
        the firmware docs off-by-one note).
        """
        if self.txsent:
            return round(100.0 * (self.txok or 0) / self.txsent, 1)
        return None


class CommandLog(models.Model):
    STATUS_PENDING = "pending"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_TIMEOUT = "timeout"
    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending")),
        (STATUS_SUCCESS, _("Success")),
        (STATUS_FAILED, _("Failed")),
        (STATUS_TIMEOUT, _("Timeout")),
    ]
    # Terminal states: the command lifecycle has concluded.
    TERMINAL_STATUSES = (STATUS_SUCCESS, STATUS_FAILED, STATUS_TIMEOUT)

    device = models.ForeignKey(
        Device, on_delete=models.CASCADE, related_name="commands"
    )
    command = models.JSONField()
    sent_at = models.DateTimeField(auto_now_add=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING
    )
    response_message = models.CharField(max_length=256, blank=True, default="")
    # acked stays True once the command reaches any terminal state (kept for
    # the wake-up flush query, which re-sends only still-pending commands).
    acked = models.BooleanField(default=False)
    acked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-sent_at"]

    def __str__(self):
        return f"{self.device_id} {self.action or '-'} [{self.status}] @ {self.sent_at:%Y-%m-%d %H:%M}"

    @property
    def action(self):
        return (self.command or {}).get("action", "")

    def mark(self, status, message="", when=None):
        """Move the command to a terminal state and record an optional message."""
        self.status = status
        self.response_message = (message or "")[:256]
        self.acked = status in self.TERMINAL_STATUSES
        self.acked_at = when or timezone.now()
        self.save(update_fields=["status", "response_message", "acked", "acked_at"])

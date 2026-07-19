from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import CommandLog, Device, DeviceDiagLog


class OnlineFilter(admin.SimpleListFilter):
    title = _("online status")
    parameter_name = "online"

    def lookups(self, request, model_admin):
        return [("yes", _("Online")), ("no", _("Offline"))]

    def queryset(self, request, queryset):
        # Filter in Python since is_online is a computed property
        if self.value() == "yes":
            return queryset.filter(
                pk__in=[d.pk for d in queryset if d.is_online]
            )
        if self.value() == "no":
            return queryset.filter(
                pk__in=[d.pk for d in queryset if not d.is_online]
            )
        return queryset


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = (
        "device_id", "device_type", "display_name", "location",
        "is_online", "is_approved", "alert_level", "battery_percent",
        "fw_version", "publish_interval", "last_seen",
    )
    list_filter = ("device_type", OnlineFilter, "is_approved", "alert_level", "ota_capable")
    search_fields = ("device_id", "display_name", "location", "hardware_id", "hardware_code__hw_code")
    readonly_fields = ("hardware_id", "hardware_code", "hw_rev", "ota_capable", "fw_version", "battery_percent")


@admin.register(CommandLog)
class CommandLogAdmin(admin.ModelAdmin):
    list_display = ("device", "action", "sent_at", "sent_by", "status", "acked_at")
    list_filter = ("status",)
    search_fields = ("device__device_id", "response_message")


@admin.register(DeviceDiagLog)
class DeviceDiagLogAdmin(admin.ModelAdmin):
    list_display = (
        "device", "time", "level", "message", "reset_cause",
        "boot", "miss", "seq", "rssi", "heap",
    )
    list_filter = ("level", "device")
    search_fields = ("device__device_id", "message")
    date_hierarchy = "time"

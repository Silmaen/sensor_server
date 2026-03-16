from django.contrib import admin

from .models import CommandLog, Device


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("device_id", "device_type", "display_name", "location", "is_online", "last_seen")
    list_filter = ("device_type", "is_online")
    search_fields = ("device_id", "display_name", "location")


@admin.register(CommandLog)
class CommandLogAdmin(admin.ModelAdmin):
    list_display = ("device", "sent_at", "sent_by", "acked")
    list_filter = ("acked",)

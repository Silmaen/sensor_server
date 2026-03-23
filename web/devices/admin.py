from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import CommandLog, Device


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
        "is_online", "is_approved", "alert_level", "publish_interval", "last_seen",
    )
    list_filter = ("device_type", OnlineFilter, "is_approved", "alert_level")
    search_fields = ("device_id", "display_name", "location")


@admin.register(CommandLog)
class CommandLogAdmin(admin.ModelAdmin):
    list_display = ("device", "sent_at", "sent_by", "acked")
    list_filter = ("acked",)

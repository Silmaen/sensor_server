from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from .models import SensorReading


@admin.register(SensorReading)
class SensorReadingAdmin(admin.ModelAdmin):
    list_display = ("time", "device_id", "metric", "value")
    list_filter = ("metric", "device_id")
    date_hierarchy = "time"
    actions = ["delete_readings"]

    # SensorReading is a TimescaleDB hypertable keyed on `time` (a DateTimeField).
    # Rows are not individually editable (read-only), but admins can delete
    # aberrant readings via the action below.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        # Drop the built-in delete_selected: on a DateTimeField primary key its
        # confirmation page re-posts the pk through a localized template
        # (e.g. "juil. 10, 2026, 8:27 après-midi") — minute-truncated and in the
        # active locale — which is lossy and unparseable, raising a 500. The custom
        # action deletes directly from the initial selection (full-ISO pks), so it
        # avoids that round-trip.
        actions.pop("delete_selected", None)
        return actions

    @admin.action(description=_("Delete selected readings"), permissions=["delete"])
    def delete_readings(self, request, queryset):
        count = queryset.count()
        queryset.delete()
        self.message_user(
            request, _("%(n)d reading(s) deleted.") % {"n": count}, messages.SUCCESS
        )

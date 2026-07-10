from django.contrib import admin

from .models import SensorReading


@admin.register(SensorReading)
class SensorReadingAdmin(admin.ModelAdmin):
    list_display = ("time", "device_id", "metric", "value")
    list_filter = ("metric", "device_id")
    date_hierarchy = "time"
    # SensorReading is a TimescaleDB hypertable keyed on `time` (a DateTimeField),
    # holding time-series rows managed by retention/compression policies. It is
    # read-only in the admin: browse/filter only. Mutating actions are disabled —
    # besides being undesirable on a hypertable, a DateTimeField primary key does
    # not round-trip through the admin action machinery (the selected pk is posted
    # back localized, e.g. "juil. 10, 2026, 8:27 après-midi", and fails to parse).
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

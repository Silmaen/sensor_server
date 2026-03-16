from django.contrib import admin

from .models import SensorReading


@admin.register(SensorReading)
class SensorReadingAdmin(admin.ModelAdmin):
    list_display = ("time", "device_id", "metric", "value")
    list_filter = ("metric", "device_id")
    date_hierarchy = "time"

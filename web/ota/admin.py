from django.contrib import admin

from .models import Firmware, HardwareCode, HardwareRevision


class HardwareRevisionInline(admin.TabularInline):
    model = HardwareRevision
    extra = 0


@admin.register(HardwareCode)
class HardwareCodeAdmin(admin.ModelAdmin):
    list_display = ("hw_code", "platform", "description", "updated_at")
    search_fields = ("hw_code", "platform", "description")
    inlines = [HardwareRevisionInline]


@admin.register(HardwareRevision)
class HardwareRevisionAdmin(admin.ModelAdmin):
    list_display = ("hardware_code", "hw_rev", "description", "bat_divider_nominal")
    list_filter = ("hardware_code",)
    search_fields = ("hardware_code__hw_code", "description")


@admin.register(Firmware)
class FirmwareAdmin(admin.ModelAdmin):
    list_display = ("hardware_revision", "version", "md5", "size", "uploaded_at")
    list_filter = ("hardware_revision__hardware_code",)
    search_fields = ("hardware_revision__hardware_code__hw_code", "version", "md5")
    readonly_fields = ("md5", "size", "uploaded_at")

from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from .models import ApiKey


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "prefix", "is_active", "created_at", "last_used_at")
    list_filter = ("is_active",)
    search_fields = ("name", "prefix")
    readonly_fields = ("prefix", "key_hash", "created_by", "created_at", "last_used_at")

    def get_fields(self, request, obj=None):
        if obj is None:
            # Creation form: operator only chooses the label and active flag.
            return ("name", "is_active")
        return (
            "name", "is_active", "prefix", "key_hash",
            "created_by", "created_at", "last_used_at",
        )

    def save_model(self, request, obj, form, change):
        if not change:
            # Generate the token on creation and surface it once.
            raw = obj.set_random_token()
            obj.created_by = request.user
            self.message_user(
                request,
                _(
                    "API key created. Copy it now — it will not be shown again: %(token)s"
                ) % {"token": raw},
                level=messages.WARNING,
            )
        super().save_model(request, obj, form, change)

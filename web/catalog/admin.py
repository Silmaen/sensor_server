from django import forms
from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import SensorDesign, SensorImage


class SensorImageInline(admin.TabularInline):
    model = SensorImage
    extra = 1
    readonly_fields = ("url_hint",)

    @admin.display(description=_("Markdown reference"))
    def url_hint(self, obj):
        """Show the URL to paste into the Markdown body as an image."""
        if not obj.pk or not obj.image:
            return "—"
        return format_html("<code>![]({})</code>", obj.image.url)


class SensorDesignForm(forms.ModelForm):
    class Meta:
        model = SensorDesign
        fields = "__all__"
        help_texts = {
            "body": _(
                "Markdown. Use a ```mermaid``` fenced block for diagrams, and "
                "reference uploaded images by their URL (shown next to each image below)."
            ),
        }
        widgets = {
            "body": forms.Textarea(attrs={"rows": 24, "style": "font-family:monospace;"}),
        }


@admin.register(SensorDesign)
class SensorDesignAdmin(admin.ModelAdmin):
    form = SensorDesignForm
    list_display = ("name", "status", "updated_at")
    list_filter = ("status", "hardware_codes")
    search_fields = ("name", "summary", "body")
    prepopulated_fields = {"slug": ("name",)}
    filter_horizontal = ("hardware_codes",)
    inlines = [SensorImageInline]

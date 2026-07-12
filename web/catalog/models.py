"""Catalog of designed sensors — hardware design docs authored in Markdown.

A ``SensorDesign`` documents a sensor the owner has designed. It links to one or
more ``ota.HardwareCode`` entries; through those the design surfaces both the
published firmwares (``HardwareRevision.firmwares``) and the real deployed units
(``Device.hardware_code``). No device/firmware relation is stored directly — it
is always derived from the hardware codes.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from .markdown import render_markdown


class SensorDesign(models.Model):
    STATUS_CHOICES = [
        ("draft", _("Draft")),
        ("active", _("Active")),
        ("deprecated", _("Deprecated")),
    ]

    name = models.CharField(max_length=128)
    slug = models.SlugField(max_length=140, unique=True)
    summary = models.CharField(max_length=256, blank=True, default="")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="draft")
    # Markdown source. Supports ```mermaid``` fenced diagrams and images
    # uploaded via SensorImage (reference them by their URL).
    body = models.TextField(blank=True, default="")
    cover = models.ImageField(upload_to="catalog/covers/", null=True, blank=True)
    hardware_codes = models.ManyToManyField(
        "ota.HardwareCode", blank=True, related_name="sensor_designs"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _("sensor design")
        verbose_name_plural = _("sensor designs")

    def __str__(self):
        return self.name

    @property
    def body_html(self):
        return render_markdown(self.body)


def sensor_image_path(instance, filename):
    return f"catalog/{instance.design.slug}/{filename}"


class SensorImage(models.Model):
    """An image uploaded for a design, referenceable from its Markdown body."""

    design = models.ForeignKey(
        SensorDesign, on_delete=models.CASCADE, related_name="images"
    )
    image = models.ImageField(upload_to=sensor_image_path)
    caption = models.CharField(max_length=256, blank=True, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]
        verbose_name = _("sensor image")
        verbose_name_plural = _("sensor images")

    def __str__(self):
        return self.caption or self.image.name

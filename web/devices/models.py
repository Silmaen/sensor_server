from django.conf import settings
from django.db import models
from django.utils import timezone


class Device(models.Model):
    device_id = models.CharField(max_length=128, primary_key=True)
    device_type = models.CharField(max_length=64, default="unknown")
    display_name = models.CharField(max_length=128, blank=True, default="")
    location = models.CharField(max_length=128, blank=True, default="")
    config = models.JSONField(default=dict, blank=True)
    is_online = models.BooleanField(default=False)
    last_seen = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["device_id"]

    def __str__(self):
        return self.display_name or self.device_id

    @property
    def effective_name(self):
        return self.display_name or self.device_id


class CommandLog(models.Model):
    device = models.ForeignKey(
        Device, on_delete=models.CASCADE, related_name="commands"
    )
    command = models.JSONField()
    sent_at = models.DateTimeField(auto_now_add=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    acked = models.BooleanField(default=False)
    acked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-sent_at"]

    def __str__(self):
        return f"{self.device_id} @ {self.sent_at:%Y-%m-%d %H:%M}"

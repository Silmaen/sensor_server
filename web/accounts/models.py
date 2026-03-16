from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class UserProfile(models.Model):
    ROLE_CHOICES = [
        ("guest", _("Guest")),
        ("resident", _("Resident")),
        ("admin", _("Administrator")),
    ]
    ROLE_HIERARCHY = {"guest": 0, "resident": 1, "admin": 2}

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    role = models.CharField(
        max_length=10, choices=ROLE_CHOICES, null=True, blank=True, default=None
    )

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display() or _('pending')})"

    def has_role(self, required_role: str) -> bool:
        if self.role is None:
            return False
        return self.ROLE_HIERARCHY.get(self.role, -1) >= self.ROLE_HIERARCHY.get(
            required_role, 99
        )

    @property
    def is_approved(self) -> bool:
        return self.role is not None

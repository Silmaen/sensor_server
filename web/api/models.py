import hashlib
import secrets

from django.conf import settings
from django.db import models


# Number of random characters (after the prefix) kept in `prefix` for display.
_PREFIX_VISIBLE_CHARS = 6


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest used to store and look up a token.

    Tokens are high-entropy random strings, so a fast unsalted digest is
    sufficient for constant-time lookup without exposing the raw value.
    """
    return hashlib.sha256(token.encode()).hexdigest()


class ApiKey(models.Model):
    """API key granting an external service read-only access to the public API.

    The raw token is shown only once, at creation time; only its SHA-256 hash
    is persisted. Clients authenticate with an ``Authorization: Bearer <token>``
    header.
    """

    TOKEN_PREFIX = "sk_"

    name = models.CharField(
        max_length=128,
        help_text="Human-readable label for the consuming service.",
    )
    prefix = models.CharField(
        max_length=16, editable=False, db_index=True,
        help_text="Leading characters of the token, for identification.",
    )
    key_hash = models.CharField(max_length=64, editable=False, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
    )
    last_used_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "API key"
        verbose_name_plural = "API keys"

    def __str__(self):
        return f"{self.name} ({self.prefix}…)"

    def set_random_token(self) -> str:
        """Assign a fresh random token to this instance and return the raw value.

        Only the hash is stored on the instance; the caller must surface the
        returned raw token to the operator, as it cannot be recovered later.
        """
        raw = self.TOKEN_PREFIX + secrets.token_urlsafe(32)
        self.prefix = raw[: len(self.TOKEN_PREFIX) + _PREFIX_VISIBLE_CHARS]
        self.key_hash = hash_token(raw)
        return raw

    @classmethod
    def generate(cls, name, created_by=None):
        """Create and persist a new key. Returns ``(instance, raw_token)``."""
        instance = cls(name=name, created_by=created_by)
        raw = instance.set_random_token()
        instance.save()
        return instance, raw

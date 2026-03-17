import logging

from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from .models import UserProfile

logger = logging.getLogger(__name__)


class SensorOIDCBackend(OIDCAuthenticationBackend):
    def filter_users_by_claims(self, claims):
        """Match existing users by email (more stable than sub)."""
        email = claims.get("email")
        if email:
            return self.UserModel.objects.filter(email=email)
        return self.UserModel.objects.none()

    def create_user(self, claims):
        logger.info("OIDC create_user claims: %s", claims)
        email = claims.get("email", "")
        username = self._extract_username(claims, email)
        user = self.UserModel.objects.create_user(username=username, email=email)
        self._update_user_from_claims(user, claims)
        UserProfile.objects.get_or_create(user=user)
        return user

    def update_user(self, user, claims):
        logger.info("OIDC update_user claims: %s", claims)
        self._update_user_from_claims(user, claims)
        UserProfile.objects.get_or_create(user=user)
        return user

    def _extract_username(self, claims, fallback=""):
        """Pick the best username from claims, avoiding email as username."""
        for key in ("preferred_username", "nickname", "name"):
            value = claims.get(key, "")
            if value and "@" not in value:
                return value
        # Last resort: local part of email
        email = claims.get("email", fallback)
        if "@" in email:
            return email.split("@")[0]
        return email or "user"

    def _update_user_from_claims(self, user, claims):
        new_username = self._extract_username(claims, user.username)
        # Only update username if it looks like it was set from sub/email
        if "@" in user.username or len(user.username) > 30:
            user.username = new_username
        user.first_name = claims.get("given_name", user.first_name or "")
        user.last_name = claims.get("family_name", user.last_name or "")
        user.email = claims.get("email", user.email)
        # Build a display name from "name" claim if first/last are empty
        if not user.first_name and not user.last_name:
            full_name = claims.get("name", "")
            if full_name:
                parts = full_name.split(None, 1)
                user.first_name = parts[0]
                user.last_name = parts[1] if len(parts) > 1 else ""
        user.save(update_fields=["username", "first_name", "last_name", "email"])

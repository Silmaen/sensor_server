from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from .models import UserProfile


class SensorOIDCBackend(OIDCAuthenticationBackend):
    def filter_users_by_claims(self, claims):
        """Match existing users by email (more stable than sub)."""
        email = claims.get("email")
        if email:
            return self.UserModel.objects.filter(email=email)
        return self.UserModel.objects.none()

    def create_user(self, claims):
        email = claims.get("email", "")
        username = claims.get("preferred_username", email)
        user = self.UserModel.objects.create_user(username=username, email=email)
        self._update_user_from_claims(user, claims)
        UserProfile.objects.get_or_create(user=user)
        return user

    def update_user(self, user, claims):
        self._update_user_from_claims(user, claims)
        UserProfile.objects.get_or_create(user=user)
        return user

    def _update_user_from_claims(self, user, claims):
        user.first_name = claims.get("given_name", "")
        user.last_name = claims.get("family_name", "")
        user.email = claims.get("email", user.email)
        user.save(update_fields=["first_name", "last_name", "email"])

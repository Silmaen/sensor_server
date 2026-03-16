from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from .models import UserProfile


class SensorOIDCBackend(OIDCAuthenticationBackend):
    def create_user(self, claims):
        user = super().create_user(claims)
        UserProfile.objects.get_or_create(user=user)
        return user

    def update_user(self, user, claims):
        user = super().update_user(user, claims)
        UserProfile.objects.get_or_create(user=user)
        return user

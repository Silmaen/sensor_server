from django.conf import settings as django_settings


def user_role(request):
    base = {
        "LOGIN_URL": django_settings.LOGIN_URL,
        "OIDC_ENABLED": django_settings.OIDC_ENABLED,
    }

    if not request.user.is_authenticated:
        return {**base, "user_role": None, "is_approved": False}

    if request.user.is_superuser:
        return {**base, "user_role": "admin", "is_approved": True}

    profile = getattr(request.user, "profile", None)
    if profile is None:
        return {**base, "user_role": None, "is_approved": False}

    return {**base, "user_role": profile.role, "is_approved": profile.is_approved}

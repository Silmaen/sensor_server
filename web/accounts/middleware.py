from django.shortcuts import redirect
from django.urls import reverse

from .models import UserProfile

# Paths that unapproved users can still access
EXEMPT_PATHS = [
    "/healthz/",
    "/oidc/",
    "/admin/",
    "/i18n/",
    "/accounts/login/",
    "/accounts/pending/",
    "/accounts/logout/",
    "/static/",
]


class RoleMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            return self.get_response(request)

        # Superusers bypass role check
        if request.user.is_superuser:
            return self.get_response(request)

        # Allow exempt paths
        if any(request.path.startswith(p) for p in EXEMPT_PATHS):
            return self.get_response(request)

        # Ensure profile exists
        profile, _ = UserProfile.objects.get_or_create(user=request.user)

        if not profile.is_approved:
            return redirect(reverse("accounts:pending"))

        return self.get_response(request)

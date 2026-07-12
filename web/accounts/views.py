from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .decorators import role_required
from .models import UserProfile


def login_view(request):
    """Local login — always available, even when OIDC is enabled."""
    error = None
    if request.method == "POST":
        username = request.POST.get("username", "")
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            next_url = request.GET.get("next", "/")
            if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                next_url = "/"
            return redirect(next_url)
        error = _("Invalid credentials.")

    return render(request, "accounts/login.html", {"error": error})


@login_required
def pending_view(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if profile.is_approved:
        return redirect("/")
    return render(request, "accounts/pending.html")


@require_POST
def logout_view(request):
    logout(request)
    return redirect("/")


@role_required("admin")
def user_list_view(request):
    profiles = UserProfile.objects.select_related("user").order_by("role", "user__username")
    pending = profiles.filter(role__isnull=True)
    approved = profiles.exclude(role__isnull=True)
    return render(
        request,
        "accounts/user_list.html",
        {
            "pending": pending,
            "approved": approved,
            "env_superuser": settings.DJANGO_SUPERUSER_USERNAME,
        },
    )


def _is_env_superuser(user) -> bool:
    """True for the bootstrap superuser defined in .env (never web-modifiable).

    Other superusers may be granted via the UI, so protection keys on the
    env-defined username rather than on ``is_superuser`` alone.
    """
    return user.username == settings.DJANGO_SUPERUSER_USERNAME


@role_required("admin")
def user_set_role_view(request, user_id):
    if request.method != "POST":
        return HttpResponseForbidden()
    profile = get_object_or_404(UserProfile, user_id=user_id)

    # Protect the env-defined superuser from role changes
    if _is_env_superuser(profile.user):
        return HttpResponseForbidden(_("Cannot modify the superuser account."))

    role = request.POST.get("role")
    if role in dict(UserProfile.ROLE_CHOICES):
        profile.role = role
        profile.save()
    elif role == "revoke":
        profile.role = None
        profile.save()
    return redirect("accounts:user_list")


@require_POST
def user_set_superadmin_view(request, user_id):
    """Grant/revoke Django admin access (is_staff + is_superuser).

    Restricted to existing superusers: only a superuser may create another one,
    which prevents privilege escalation from the ``admin`` role. The env-defined
    superuser cannot be demoted from the web UI.
    """
    if not request.user.is_superuser:
        return HttpResponseForbidden(_("You do not have the required permissions."))

    profile = get_object_or_404(UserProfile, user_id=user_id)
    target = profile.user

    if _is_env_superuser(target):
        return HttpResponseForbidden(_("Cannot modify the superuser account."))

    action = request.POST.get("action")
    if action == "grant":
        target.is_staff = True
        target.is_superuser = True
        target.save(update_fields=["is_staff", "is_superuser"])
        # A superuser must be an approved user; default to the admin role.
        if profile.role is None:
            profile.role = "admin"
            profile.save(update_fields=["role"])
    elif action == "revoke":
        target.is_staff = False
        target.is_superuser = False
        target.save(update_fields=["is_staff", "is_superuser"])
    return redirect("accounts:user_list")

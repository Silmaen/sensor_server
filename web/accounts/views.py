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
        {"pending": pending, "approved": approved},
    )


@role_required("admin")
def user_set_role_view(request, user_id):
    if request.method != "POST":
        return HttpResponseForbidden()
    profile = get_object_or_404(UserProfile, user_id=user_id)
    role = request.POST.get("role")
    if role in dict(UserProfile.ROLE_CHOICES):
        profile.role = role
        profile.save()
    elif role == "revoke":
        profile.role = None
        profile.save()
    return redirect("accounts:user_list")

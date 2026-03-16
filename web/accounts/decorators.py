from functools import wraps

from django.http import HttpResponseForbidden
from django.utils.translation import gettext as _


def role_required(minimum_role):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                from django.shortcuts import redirect
                from django.conf import settings

                return redirect(settings.LOGIN_URL)

            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)

            profile = getattr(request.user, "profile", None)
            if profile is None or not profile.has_role(minimum_role):
                return HttpResponseForbidden(
                    _("You do not have the required permissions.")
                )

            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator

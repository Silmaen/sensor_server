import hmac
from functools import wraps

from django.conf import settings
from django.http import JsonResponse


def _extract_bearer_token(request):
    """Return the Bearer token from the Authorization header, or ""."""
    header = request.META.get("HTTP_AUTHORIZATION", "")
    prefix = "Bearer "
    if header.startswith(prefix):
        return header[len(prefix):].strip()
    return ""


def publish_token_required(view_func):
    """Require the OTA publication token (``Authorization: Bearer <token>``).

    The token is a single shared secret in ``settings.OTA_PUBLISH_TOKEN`` (env),
    distinct from the read-only ``api.ApiKey`` and intended for a non-interactive
    publisher (CI). If it is unset, publication is disabled (503).
    """

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        expected = getattr(settings, "OTA_PUBLISH_TOKEN", "") or ""
        if not expected:
            return JsonResponse(
                {"error": "publication_disabled",
                 "detail": "OTA_PUBLISH_TOKEN is not configured."},
                status=503,
            )
        provided = _extract_bearer_token(request)
        if not provided or not hmac.compare_digest(provided, expected):
            response = JsonResponse(
                {"error": "unauthorized",
                 "detail": "Invalid or missing publish token."},
                status=401,
            )
            response["WWW-Authenticate"] = 'Bearer realm="ota-publish"'
            return response
        return view_func(request, *args, **kwargs)

    return _wrapped

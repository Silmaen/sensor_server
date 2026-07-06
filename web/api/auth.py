from functools import wraps

from django.http import JsonResponse
from django.utils import timezone

from .models import ApiKey, hash_token


def _extract_bearer_token(request):
    """Return the Bearer token from the Authorization header, or ""."""
    header = request.META.get("HTTP_AUTHORIZATION", "")
    prefix = "Bearer "
    if header.startswith(prefix):
        return header[len(prefix):].strip()
    return ""


def _unauthorized(detail):
    response = JsonResponse({"error": "unauthorized", "detail": detail}, status=401)
    # Advertise the expected scheme per RFC 6750.
    response["WWW-Authenticate"] = 'Bearer realm="sensor-api"'
    return response


def api_key_required(view_func):
    """Require a valid, active API key supplied as an Authorization Bearer token.

    On success, attaches the matched :class:`ApiKey` to ``request.api_key`` and
    records its usage timestamp.
    """

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        token = _extract_bearer_token(request)
        if not token:
            return _unauthorized("Missing Bearer token.")
        try:
            key = ApiKey.objects.get(key_hash=hash_token(token), is_active=True)
        except ApiKey.DoesNotExist:
            return _unauthorized("Invalid or inactive API key.")

        # Best-effort usage tracking without touching other fields.
        ApiKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now())
        request.api_key = key
        return view_func(request, *args, **kwargs)

    return _wrapped

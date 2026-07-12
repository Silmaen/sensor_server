from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.utils.translation import gettext_lazy as _

admin.site.site_header = _("IoT Sensors — Administration")
admin.site.site_title = _("IoT Sensors admin")
admin.site.index_title = _("Administration")


def healthz(request):
    """Health check endpoint for Docker / load balancers."""
    from django.db import connection

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False

    healthy = db_ok
    status = 200 if healthy else 503
    return JsonResponse({"status": "ok" if healthy else "degraded", "db": db_ok}, status=status)


urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("accounts/", include("accounts.urls")),
    path("devices/", include("devices.urls")),
    path("api/v1/", include("api.urls")),
    path("api/", include("ota.urls")),
    path("ota/", include("ota.web_urls")),
    path("catalog/", include("catalog.urls")),
    path("", include("readings.urls")),
]

if settings.OIDC_ENABLED:
    urlpatterns.insert(2, path("oidc/", include("mozilla_django_oidc.urls")))

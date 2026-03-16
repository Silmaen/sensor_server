from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("accounts/", include("accounts.urls")),
    path("devices/", include("devices.urls")),
    path("", include("readings.urls")),
]

if settings.OIDC_ENABLED:
    urlpatterns.insert(1, path("oidc/", include("mozilla_django_oidc.urls")))

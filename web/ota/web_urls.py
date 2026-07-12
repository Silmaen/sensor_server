"""Human-facing OTA pages (site UI), kept separate from the CI publication API.

The API in ``ota.urls`` is mounted under ``/api/``; these pages are mounted
under ``/ota/`` in the project urlconf.
"""

from django.urls import path

from . import views

app_name = "ota_web"

urlpatterns = [
    path("firmwares/", views.firmware_overview_view, name="firmware_overview"),
]

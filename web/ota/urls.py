from django.urls import path

from . import views

app_name = "ota"

urlpatterns = [
    # A5 must precede A3/A4 so "firmwares/latest" is not shadowed.
    path("firmwares/latest", views.firmware_latest_view, name="firmware_latest"),
    path("firmwares", views.firmwares_view, name="firmwares"),
    path("hw/codes/<str:hw_code>/revs/<int:hw_rev>", views.hw_revision_view, name="hw_revision"),
    path("hw/codes/<str:hw_code>", views.hw_code_view, name="hw_code"),
]

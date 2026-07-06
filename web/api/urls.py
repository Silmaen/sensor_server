from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("devices/", views.devices_view, name="devices"),
    path("readings/", views.readings_view, name="readings"),
    path("aggregates/", views.aggregates_view, name="aggregates"),
]

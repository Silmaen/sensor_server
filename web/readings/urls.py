from django.urls import path

from . import views

app_name = "readings"

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("api/chart/<str:device_id>/", views.chart_data_view, name="chart_data"),
    path("api/chart/<str:device_id>/hourly/", views.chart_data_hourly_view, name="chart_data_hourly"),
]

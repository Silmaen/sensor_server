from django.urls import path

from . import views

app_name = "readings"

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("cards/", views.dashboard_cards_view, name="dashboard_cards"),
    path("overview/", views.overview_view, name="overview"),
    path("api/chart/<str:device_id>/", views.chart_data_view, name="chart_data"),
    path("api/status-timeline/<str:device_id>/", views.status_timeline_view, name="status_timeline"),
    path("api/overview-chart/", views.overview_chart_data_view, name="overview_chart_data"),
    path("api/delete-readings/<str:device_id>/", views.delete_readings_view, name="delete_readings"),
]

from django.urls import path

from . import views

app_name = "readings"

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("cards/", views.dashboard_cards_view, name="dashboard_cards"),
    path("api/chart/<str:device_id>/", views.chart_data_view, name="chart_data"),
]

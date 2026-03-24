from django.urls import path

from . import views

app_name = "devices"

urlpatterns = [
    path("", views.device_list_view, name="list"),
    path("<str:device_id>/", views.device_history_view, name="history"),
    path("<str:device_id>/admin/", views.device_admin_view, name="admin"),
    path("<str:device_id>/edit/", views.device_edit_view, name="edit"),
    path("<str:device_id>/command/", views.device_command_view, name="command"),
    path("<str:device_id>/approve/", views.device_approve_view, name="approve"),
    path("<str:device_id>/request-capabilities/", views.device_request_capabilities_view, name="request_capabilities"),
]

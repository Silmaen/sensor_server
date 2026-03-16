from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("pending/", views.pending_view, name="pending"),
    path("logout/", views.logout_view, name="logout"),
    path("users/", views.user_list_view, name="user_list"),
    path("users/<int:user_id>/set-role/", views.user_set_role_view, name="user_set_role"),
]

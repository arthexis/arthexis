"""Root route provider for user/account HTTP entry points."""

from django.urls import include, path

from config.admin_urls import admin_route

from .admin_views import request_temp_password

ROOT_URLPATTERNS = [
    path("core/impersonation/", include("apps.users.urls")),
    path(
        admin_route("request-temp-password/"),
        request_temp_password,
        name="admin-request-temp-password",
    ),
]

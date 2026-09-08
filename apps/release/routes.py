"""Root route provider for release-owned HTTP entry points."""

from django.urls import include, path

from config.admin_urls import admin_route

ROOT_URLPATTERNS = [
    path(
        admin_route("core/releases/"),
        include("apps.release.urls"),
    ),
]

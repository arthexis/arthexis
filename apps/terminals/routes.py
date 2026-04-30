"""Root route provider for app-owned URL mounts."""

from django.urls import include, path

ROOT_URLPATTERNS = [
    path("terminals/", include("apps.terminals.urls")),
]

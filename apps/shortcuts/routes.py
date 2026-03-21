"""Root route provider for shortcut management endpoints."""

from django.urls import include, path

ROOT_URLPATTERNS = [
    path("shortcuts/", include("apps.shortcuts.urls")),
]

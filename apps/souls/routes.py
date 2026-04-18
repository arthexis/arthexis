"""Root route provider for soul registration endpoints."""

from django.urls import include, path

ROOT_URLPATTERNS = [
    path("soul/", include("apps.souls.urls")),
]

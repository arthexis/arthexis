"""Root route provider for imager-owned URL mounts."""

from django.urls import include, path

ROOT_URLPATTERNS = [
    path("imager/", include("apps.imager.urls")),
]

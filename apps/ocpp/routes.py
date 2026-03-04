"""Root route provider for app-owned URL mounts."""

from django.urls import include, path

ROOT_URLPATTERNS = [
    path("ocpp/", include("apps.ocpp.urls")),
]

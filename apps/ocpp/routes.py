"""Root route provider for app-owned URL mounts."""

from django.conf import settings
from django.urls import include, path

ROOT_URLPATTERNS = [
    path("ocpp/", include("apps.ocpp.urls")),
]

if settings.CHARGER_INTAKE_PUBLIC_ROUTES_ENABLED:
    ROOT_URLPATTERNS.insert(0, path("", include("apps.ocpp.intake_urls")))

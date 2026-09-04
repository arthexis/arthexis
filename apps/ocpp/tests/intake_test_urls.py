"""Namespaced URLconf for isolated public charger intake tests."""

from django.urls import include, path

urlpatterns = [
    path("", include("apps.ocpp.intake_urls", namespace="ocpp_intake")),
]

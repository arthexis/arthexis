"""Root route provider for Netmesh APIs."""

from django.urls import include, path

ROOT_URLPATTERNS = [
    path("api/netmesh/", include("apps.netmesh.api.urls")),
]

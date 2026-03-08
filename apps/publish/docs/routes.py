"""Root route provider for docs pages."""

from django.urls import include, path

ROOT_URLPATTERNS = [
    path("", include("apps.publish.docs.urls")),
]

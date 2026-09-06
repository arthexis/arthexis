"""Root route provider for core-owned framework extensions."""

from django.urls import path

from apps.core import views as core_views

ROOT_URLPATTERNS = [
    path("version/", core_views.version_info, name="version-info"),
]

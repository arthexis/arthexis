"""Root route provider for shop pages."""

from django.urls import include, path

ROOT_URLPATTERNS = [path("", include("apps.shop.urls"))]

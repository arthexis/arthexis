"""Root route provider for blog pages."""

from django.urls import include, path

ROOT_URLPATTERNS = [
    path("", include("apps.publish.blog.urls")),
]

"""Application configuration for the blog app."""

from django.apps import AppConfig


class BlogConfig(AppConfig):
    """Configure blog app metadata for Django."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.blog"

"""Application configuration for CDN settings."""

from django.apps import AppConfig


class CdnConfig(AppConfig):
    """Register the CDN app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cdn"
    label = "cdn"

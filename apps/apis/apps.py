"""Configuration for managed service credentials."""

from django.apps import AppConfig


class ApisConfig(AppConfig):
    """Register managed service credential models in Django."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.apis"
    verbose_name = "Service Credentials"

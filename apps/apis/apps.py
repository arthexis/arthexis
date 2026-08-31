"""Configuration for the API explorer app."""

from django.apps import AppConfig


class ApisConfig(AppConfig):
    """Register API explorer models in Django."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.apis"
    verbose_name = "API Explorer"

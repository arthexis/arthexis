"""App configuration for Evergo integration."""

from django.apps import AppConfig


class EvergoConfig(AppConfig):
    """Django app configuration for Evergo models and commands."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.evergo"
    verbose_name = "Evergo"

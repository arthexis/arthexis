"""Django app configuration for shared base models."""

from django.apps import AppConfig


class BaseConfig(AppConfig):
    """Register the base application and shared model layer."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.base"

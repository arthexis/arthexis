"""App configuration for hosted JavaScript extensions."""

from django.apps import AppConfig


class ExtensionsConfig(AppConfig):
    """Configure the extensions app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.extensions"
    label = "extensions"
    verbose_name = "JS Extensions"

"""App configuration for prototype management."""

from django.apps import AppConfig


class PrototypesConfig(AppConfig):
    """Configure the prototype management app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.prototypes"
    label = "prototypes"
    verbose_name = "Prototypes"

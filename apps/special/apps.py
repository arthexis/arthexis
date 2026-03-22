"""Django application configuration for ``apps.special``."""

from django.apps import AppConfig


class SpecialConfig(AppConfig):
    """Configure metadata for the special command registry app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.special"
    verbose_name = "Special Commands"

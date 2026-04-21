from django.apps import AppConfig


class RpiconnectConfig(AppConfig):
    """Default app configuration for scaffolded local app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.rpiconnect"
    label = "rpiconnect"
    verbose_name = "Rpiconnect"

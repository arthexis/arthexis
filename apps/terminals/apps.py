from django.apps import AppConfig


class TerminalsConfig(AppConfig):
    """Default app configuration for scaffolded local app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.terminals"
    label = "terminals"
    verbose_name = "Terminals"

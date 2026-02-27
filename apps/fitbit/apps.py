"""Application config for Fitbit integration."""

from django.apps import AppConfig


class FitbitConfig(AppConfig):
    """Configuration for Fitbit integration models and commands."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.fitbit"
    verbose_name = "Fitbit"


    def ready(self) -> None:
        """Register Fitbit signal handlers."""
        from apps.fitbit import signals  # noqa: F401

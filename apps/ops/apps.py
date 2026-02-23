"""Application configuration for operations workflows."""

from django.apps import AppConfig as DjangoAppConfig


class OpsConfig(DjangoAppConfig):
    """Configure startup hooks for the operations app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ops"
    verbose_name = "Operations"

    def ready(self) -> None:
        """Load widget registrations and signal handlers."""

        from . import widgets  # noqa: F401

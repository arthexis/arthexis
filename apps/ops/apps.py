"""Django application configuration for operations workflows."""

from django.apps import AppConfig


class OpsConfig(AppConfig):
    """Register operations integrations when Django starts."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ops"
    label = "ops"

    def ready(self) -> None:  # pragma: no cover - import side-effects
        from . import widgets  # noqa: F401

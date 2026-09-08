"""Django application configuration for operations workflows."""

from django.apps import AppConfig


class OpsConfig(AppConfig):
    """Register operations integrations when Django starts."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ops"
    label = "ops"

    def ready(self):  # pragma: no cover - Django startup hook
        from . import admin_notice  # noqa: F401
        from . import admin_notice_admin  # noqa: F401

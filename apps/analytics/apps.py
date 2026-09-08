from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.analytics"
    label = "analytics"
    verbose_name = "Analytics"

    def ready(self):  # pragma: no cover - Django startup hook
        from . import analytics  # noqa: F401

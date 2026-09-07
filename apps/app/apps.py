from django.apps import AppConfig as BaseAppConfig


class AppConfig(BaseAppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.app"

    def ready(self):
        from apps.app import signals  # noqa: F401
        from apps.app.checks import apps_registry  # noqa: F401

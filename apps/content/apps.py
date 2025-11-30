from django.apps import AppConfig


class ContentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.content"
    label = "content"

    def ready(self):  # pragma: no cover - Django hook
        from . import signals  # noqa: F401

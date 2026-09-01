from django.apps import AppConfig


class NodesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.nodes"
    label = "nodes"
    def ready(self):  # pragma: no cover - exercised on app start
        # Import node signal handlers
        from . import signals  # noqa: F401

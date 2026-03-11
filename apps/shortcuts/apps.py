from django.apps import AppConfig


class ShortcutsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.shortcuts"
    verbose_name = "Shortcut Management"

    def ready(self) -> None:
        """Register application signal handlers."""

        from . import signals  # noqa: F401

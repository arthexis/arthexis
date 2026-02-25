"""Application configuration for desktop assistant features."""

from django.apps import AppConfig


class DesktopConfig(AppConfig):
    """Configure desktop assistant extension registry support."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.desktop"
    verbose_name = "Desktop Assistant"

"""Application configuration for desktop shortcut features."""

from django.apps import AppConfig


class DesktopConfig(AppConfig):
    """Configure desktop shortcut synchronization support."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.desktop"
    verbose_name = "Desktop Shortcuts"

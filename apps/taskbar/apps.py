"""Application configuration for taskbar integration."""

from django.apps import AppConfig


class TaskbarConfig(AppConfig):
    """Configure the taskbar Django app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.taskbar"
    verbose_name = "Taskbar"

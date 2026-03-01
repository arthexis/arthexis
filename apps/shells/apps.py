"""Application configuration for shell script inventory."""

from django.apps import AppConfig


class ShellsConfig(AppConfig):
    """Configure the shell scripts inventory app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.shells"
    label = "shells"
    verbose_name = "Shell Scripts"

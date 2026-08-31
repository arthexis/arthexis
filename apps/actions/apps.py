"""App configuration for remote actions."""

from django.apps import AppConfig


class ActionsConfig(AppConfig):
    """Configure the remote actions app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.actions"
    label = "actions"
    verbose_name = "Remote Actions"

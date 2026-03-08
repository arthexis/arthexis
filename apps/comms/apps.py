"""AppConfig for communication-related apps."""

from django.apps import AppConfig


class CommsConfig(AppConfig):
    """Top-level package app for communication modules."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.comms"
    verbose_name = "Communications"

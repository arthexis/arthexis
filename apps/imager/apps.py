"""Application configuration for Raspberry Pi image tooling."""

from django.apps import AppConfig


class ImagerConfig(AppConfig):
    """Register Raspberry Pi image artifact tooling."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.imager"
    verbose_name = "Raspberry Pi Imager"

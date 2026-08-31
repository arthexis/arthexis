"""Application config for project-local WhiteNoise integration."""

from django.apps import AppConfig


class WhitenoiseConfig(AppConfig):
    """Expose WhiteNoise compatibility hooks under an underscore-free app path."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.whitenoise"

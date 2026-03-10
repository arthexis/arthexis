"""Application configuration for game models."""

from django.apps import AppConfig


class GameConfig(AppConfig):
    """Configuration for avatar and gamification models."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.game"
    verbose_name = "Game"

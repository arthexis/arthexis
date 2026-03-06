"""Application configuration for the shop app."""

from django.apps import AppConfig


class ShopConfig(AppConfig):
    """Django app configuration for shop features."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.shop"
    verbose_name = "Shop"

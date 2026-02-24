"""App configuration for NFT support."""

from django.apps import AppConfig


class NftsConfig(AppConfig):
    """Configuration for NFT tracking and RFID identity transfer support."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.nfts"

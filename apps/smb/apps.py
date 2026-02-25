"""SMB application configuration."""

from django.apps import AppConfig


class SmbConfig(AppConfig):
    """Register SMB management models and commands."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.smb"
    verbose_name = "SMB"

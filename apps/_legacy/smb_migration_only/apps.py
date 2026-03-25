"""Application config for the legacy smb migration-only app."""

from django.apps import AppConfig


class SmbMigrationOnlyConfig(AppConfig):
    """Keep smb migrations available while runtime code stays removed."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps._legacy.smb_migration_only"
    label = "smb"
    verbose_name = "Smb (migration only)"

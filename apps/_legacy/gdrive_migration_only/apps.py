"""Application config for the legacy gdrive migration-only app."""

from django.apps import AppConfig


class GDriveMigrationOnlyConfig(AppConfig):
    """Keep the retired gdrive migration chain available for historical installs."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps._legacy.gdrive_migration_only"
    label = "gdrive"
    verbose_name = "Google Drive (migration only)"

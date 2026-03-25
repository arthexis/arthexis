"""Application config for the legacy screens migration-only app."""

from django.apps import AppConfig


class ScreensMigrationOnlyConfig(AppConfig):
    """Keep screens migrations available while runtime code stays removed."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps._legacy.screens_migration_only"
    label = "screens"
    verbose_name = "Screens (migration only)"

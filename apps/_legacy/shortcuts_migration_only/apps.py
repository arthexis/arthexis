"""Application config for the legacy shortcuts migration-only app."""

from django.apps import AppConfig


class ShortcutsMigrationOnlyConfig(AppConfig):
    """Keep shortcuts migrations available while runtime code stays removed."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps._legacy.shortcuts_migration_only"
    label = "shortcuts"
    verbose_name = "Shortcuts (migration only)"

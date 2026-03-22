"""Application config for the legacy extensions migration-only app."""

from django.apps import AppConfig


class ExtensionsMigrationOnlyConfig(AppConfig):
    """Keep extensions migrations available while runtime entrypoints stay removed."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps._legacy.extensions_migration_only"
    label = "extensions"
    verbose_name = "Extensions (migration only)"

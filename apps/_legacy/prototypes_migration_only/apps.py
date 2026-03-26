"""Application config for the legacy prototypes migration-only app."""

from django.apps import AppConfig


class PrototypesMigrationOnlyConfig(AppConfig):
    """Keep prototype migrations available while runtime code stays removed."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps._legacy.prototypes_migration_only"
    label = "prototypes"
    verbose_name = "Prototypes (migration only)"

"""Application config for the legacy apis migration-only app."""

from django.apps import AppConfig


class ApisMigrationOnlyConfig(AppConfig):
    """Keep apis migrations available while runtime code stays removed."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps._legacy.apis_migration_only"
    label = "apis"
    verbose_name = "APIs (migration only)"

"""Application config for the legacy Fitbit migration-only app."""

from django.apps import AppConfig


class FitbitMigrationOnlyConfig(AppConfig):
    """Keep Fitbit migrations available after removing the runtime app package."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps._legacy.fitbit_migration_only"
    label = "fitbit"
    verbose_name = "Fitbit (migration only)"

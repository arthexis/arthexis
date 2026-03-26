"""Manifest entries for the legacy Fitbit migration-only app."""

# Keep this declarative mirror synchronized with config/settings/apps.py:
# LEGACY_MIGRATION_APPS.

DJANGO_APPS = [
    "apps._legacy.fitbit_migration_only.apps.FitbitMigrationOnlyConfig",
]

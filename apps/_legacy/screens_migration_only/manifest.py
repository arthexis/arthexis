"""Manifest entries for the legacy screens migration-only app."""

# Keep this declarative mirror synchronized with config/settings/apps.py:
# LEGACY_MIGRATION_APPS.

DJANGO_APPS = [
    "apps._legacy.screens_migration_only.apps.ScreensMigrationOnlyConfig",
]

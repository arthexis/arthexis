"""Manifest entries for the legacy selenium migration-only app."""

# Keep this declarative mirror synchronized with config/settings/apps.py:
# LEGACY_MIGRATION_APPS.

DJANGO_APPS = [
    "apps._legacy.selenium_migration_only.apps.SeleniumMigrationOnlyConfig",
]

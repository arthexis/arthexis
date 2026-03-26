"""Manifest entries for the legacy shortcuts migration-only app."""

# Keep this declarative mirror synchronized with config/settings/apps.py:
# LEGACY_MIGRATION_APPS.

DJANGO_APPS = [
    "apps._legacy.shortcuts_migration_only.apps.ShortcutsMigrationOnlyConfig",
]

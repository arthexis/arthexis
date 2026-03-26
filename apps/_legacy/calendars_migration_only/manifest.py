"""Manifest entries for the legacy calendars migration-only app."""

# Keep this declarative mirror synchronized with config/settings/apps.py:
# LEGACY_MIGRATION_APPS.

DJANGO_APPS = [
    "apps._legacy.calendars_migration_only.apps.CalendarsMigrationOnlyConfig",
]

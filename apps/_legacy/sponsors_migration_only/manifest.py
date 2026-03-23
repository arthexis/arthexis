"""Manifest entries for the legacy sponsors migration-only app."""

# Keep this declarative mirror synchronized with config/settings/apps.py:
# LEGACY_MIGRATION_APPS.

DJANGO_APPS = [
    "apps._legacy.sponsors_migration_only.apps.SponsorsMigrationOnlyConfig",
]

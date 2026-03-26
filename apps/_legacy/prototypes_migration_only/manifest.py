"""Manifest entries for the legacy prototypes migration-only app."""

# Keep this declarative mirror synchronized with config/settings/apps.py:
# LEGACY_MIGRATION_APPS.

DJANGO_APPS = [
    "apps._legacy.prototypes_migration_only.apps.PrototypesMigrationOnlyConfig",
]

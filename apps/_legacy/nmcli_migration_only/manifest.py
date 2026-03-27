"""Manifest entries for the legacy nmcli migration-only app."""

# Keep this declarative mirror synchronized with config/settings/apps.py:
# LEGACY_MIGRATION_APPS.

DJANGO_APPS = [
    "apps._legacy.nmcli_migration_only.apps.NmcliMigrationOnlyConfig",
]

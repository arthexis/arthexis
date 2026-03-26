"""Manifest entries for the legacy smb migration-only app."""

# Keep this declarative mirror synchronized with config/settings/apps.py:
# LEGACY_MIGRATION_APPS.

DJANGO_APPS = [
    "apps._legacy.smb_migration_only.apps.SmbMigrationOnlyConfig",
]

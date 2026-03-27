"""Manifest entries for legacy nmcli migration compatibility."""

DJANGO_APPS = [
    "apps._legacy.nmcli_migration_only.apps.NmcliMigrationOnlyConfig",
]

REQUIRES_APPS = [
    "apps.discovery",
]

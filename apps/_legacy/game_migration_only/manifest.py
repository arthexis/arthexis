"""Manifest entries for the legacy game migration-only app."""

# Keep this declarative mirror synchronized with config/settings/apps.py:
# LEGACY_MIGRATION_APPS.

DJANGO_APPS = [
    "apps._legacy.game_migration_only.apps.GameMigrationOnlyConfig",
]

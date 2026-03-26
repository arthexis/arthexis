"""Manifest entries for the legacy MCP migration-only app."""

# Keep this declarative mirror synchronized with config/settings/apps.py:
# LEGACY_MIGRATION_APPS.

DJANGO_APPS = [
    "apps._legacy.mcp_migration_only.apps.McpMigrationOnlyConfig",
]

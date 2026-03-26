"""Application config for the legacy MCP migration-only app."""

from django.apps import AppConfig


class McpMigrationOnlyConfig(AppConfig):
    """Keep MCP migrations available while runtime code stays removed."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps._legacy.mcp_migration_only"
    label = "mcp"
    verbose_name = "MCP (migration only)"

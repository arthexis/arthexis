from django.apps import AppConfig


class McpConfig(AppConfig):
    """Application config for MCP services and models."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.mcp"
    label = "mcp"

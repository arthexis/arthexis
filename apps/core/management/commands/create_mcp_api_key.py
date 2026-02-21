"""Compatibility command entrypoint for MCP API key creation.

Canonical implementation lives in ``apps.mcp.management.commands.create_mcp_api_key``.
"""

from __future__ import annotations

from apps.core.management.deprecation import create_deprecated_command_shim
from apps.mcp.management.commands.create_mcp_api_key import Command as McpCommand

Command = create_deprecated_command_shim(
    canonical_command=McpCommand,
    shim_path="apps.core.management.commands.create_mcp_api_key",
    canonical_path="apps.mcp.management.commands.create_mcp_api_key",
)

__all__ = ["Command", "McpCommand"]

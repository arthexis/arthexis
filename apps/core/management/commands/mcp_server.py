"""Compatibility command entrypoint for MCP server.

Canonical implementation lives in ``apps.mcp.management.commands.mcp_server``.
"""

from __future__ import annotations

from apps.core.management.deprecation import create_deprecated_command_shim
from apps.mcp.management.commands.mcp_server import Command as McpCommand

Command = create_deprecated_command_shim(
    canonical_command=McpCommand,
    shim_path="apps.core.management.commands.mcp_server",
    canonical_path="apps.mcp.management.commands.mcp_server",
)

__all__ = ["Command", "McpCommand"]

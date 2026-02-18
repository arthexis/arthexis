from __future__ import annotations

"""Compatibility command entrypoint for MCP server.

Canonical implementation lives in ``apps.mcp.management.commands.mcp_server``.
"""

from apps.mcp.management.commands.mcp_server import Command

__all__ = ["Command"]

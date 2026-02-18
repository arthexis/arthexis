from __future__ import annotations

"""Compatibility command entrypoint for MCP API key creation.

Canonical implementation lives in ``apps.mcp.management.commands.create_mcp_api_key``.
"""

from apps.mcp.management.commands.create_mcp_api_key import Command

__all__ = ["Command"]

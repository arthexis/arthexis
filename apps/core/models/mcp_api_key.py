from __future__ import annotations

"""Compatibility module for MCP API key imports.

MCP models now live in ``apps.mcp``.
"""

from apps.mcp.models import GeneratedMcpApiKey, McpApiKey

__all__ = ["GeneratedMcpApiKey", "McpApiKey"]

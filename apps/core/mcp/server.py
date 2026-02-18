from __future__ import annotations

"""Compatibility wrapper for MCP JSON-RPC server.

Use ``apps.mcp.server`` instead.
"""

from apps.mcp.server import (
    AuthenticatedMcpKey,
    DjangoCommandMCPServer,
    McpAuthenticationError,
    McpAuthorizationError,
    McpProtocolError,
    run_stdio_server,
)

__all__ = [
    "AuthenticatedMcpKey",
    "DjangoCommandMCPServer",
    "McpAuthenticationError",
    "McpAuthorizationError",
    "McpProtocolError",
    "run_stdio_server",
]

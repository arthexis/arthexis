from __future__ import annotations

"""Compatibility wrapper for MCP remote command helpers.

Use ``apps.mcp.remote_commands`` instead.
"""

from apps.mcp.remote_commands import (  # noqa: F401
    RemoteCommandError,
    RemoteCommandMetadata,
    RemoteCommandNotAllowedError,
    discover_remote_commands,
    remote_command,
)

__all__ = [
    "RemoteCommandError",
    "RemoteCommandMetadata",
    "RemoteCommandNotAllowedError",
    "discover_remote_commands",
    "remote_command",
]

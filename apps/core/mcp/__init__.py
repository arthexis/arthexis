from __future__ import annotations

"""Compatibility exports for MCP helpers moved to ``apps.mcp``."""

from apps.mcp.remote_commands import RemoteCommandMetadata, remote_command

__all__ = ["RemoteCommandMetadata", "remote_command"]

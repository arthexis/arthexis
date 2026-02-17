"""MCP utilities for exposing selected Django management commands."""

from .remote_commands import RemoteCommandMetadata, remote_command

__all__ = ["RemoteCommandMetadata", "remote_command"]

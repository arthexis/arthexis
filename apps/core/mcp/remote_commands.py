from __future__ import annotations

"""Helpers for marking and discovering Django management commands for MCP use."""

from dataclasses import dataclass
from typing import Any

from django.core.management import get_commands, load_command_class
from django.core.management.base import BaseCommand


class RemoteCommandError(RuntimeError):
    """Base error for remote command discovery/execution failures."""


class RemoteCommandNotAllowedError(RemoteCommandError):
    """Raised when a command is not allowed to be executed remotely."""


@dataclass(frozen=True)
class RemoteCommandMetadata:
    """Metadata attached to command classes marked for MCP remote execution."""

    command_name: str
    description: str
    allow_remote: bool = True


def remote_command(*, description: str) -> Any:
    """Mark a Django management command class as remotely callable via MCP.

    Args:
        description: Human-readable description surfaced in the MCP tool listing.
    """

    def _decorate(command_class: type[BaseCommand]) -> type[BaseCommand]:
        command_class._mcp_remote_metadata = {  # type: ignore[attr-defined]
            "description": description,
            "allow_remote": True,
        }
        return command_class

    return _decorate


def _command_metadata(command_name: str) -> RemoteCommandMetadata | None:
    """Return MCP metadata for a command if the command is remote-enabled."""

    command_map = get_commands()
    app_name = command_map.get(command_name)
    if app_name is None:
        return None

    command = load_command_class(app_name, command_name)
    metadata = getattr(command.__class__, "_mcp_remote_metadata", None)
    if not isinstance(metadata, dict):
        return None

    description = metadata.get("description")
    if not isinstance(description, str) or not description.strip():
        raise RemoteCommandError(
            f"Remote command '{command_name}' has invalid MCP description metadata."
        )

    allow_remote = bool(metadata.get("allow_remote", False))
    if not allow_remote:
        return None

    return RemoteCommandMetadata(
        command_name=command_name,
        description=description.strip(),
        allow_remote=allow_remote,
    )


def discover_remote_commands(
    *, allow: set[str] | None = None, deny: set[str] | None = None
) -> dict[str, RemoteCommandMetadata]:
    """Discover all management commands currently marked as MCP-remote.

    Args:
        allow: Optional explicit allow-list. If provided, only matching commands are retained.
        deny: Optional explicit deny-list. Matching commands are always excluded.
    """

    allow = allow or set()
    deny = deny or set()

    discovered: dict[str, RemoteCommandMetadata] = {}
    for command_name in sorted(get_commands().keys()):
        metadata = _command_metadata(command_name)
        if metadata is None:
            continue
        if allow and command_name not in allow:
            continue
        if command_name in deny:
            continue
        discovered[command_name] = metadata

    return discovered

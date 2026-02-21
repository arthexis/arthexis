from __future__ import annotations

"""Compatibility command entrypoint for MCP server.

Canonical implementation lives in ``apps.mcp.management.commands.mcp_server``.
"""

from typing import Any

from apps.mcp.management.commands.mcp_server import Command as McpCommand


class Command(McpCommand):
    """Backwards-compatible MCP server command shim.

    The canonical management command now lives in the ``apps.mcp`` app.
    """

    help = (
        f"{McpCommand.help} "
        "[Deprecated shim: apps.core.management.commands.mcp_server; "
        "canonical implementation is in apps.mcp.management.commands.mcp_server.]"
    )

    def handle(self, *args: Any, **options: Any) -> None:  # type: ignore[override]
        """Emit a deprecation warning and delegate to the canonical command."""

        self.stderr.write(
            self.style.WARNING(
                "Deprecation warning: apps.core.management.commands.mcp_server "
                "is a compatibility shim and will be removed in a future release. "
                "Use the canonical command in apps.mcp.management.commands.mcp_server."
            )
        )
        super().handle(*args, **options)

__all__ = ["Command"]

from __future__ import annotations

"""Compatibility command entrypoint for MCP API key creation.

Canonical implementation lives in ``apps.mcp.management.commands.create_mcp_api_key``.
"""

from typing import Any

from apps.mcp.management.commands.create_mcp_api_key import Command as McpCommand


class Command(McpCommand):
    """Backwards-compatible MCP API key command shim.

    The canonical management command now lives in the ``apps.mcp`` app.
    """

    help = (
        f"{McpCommand.help} "
        "[Deprecated shim: apps.core.management.commands.create_mcp_api_key; "
        "canonical implementation is in apps.mcp.management.commands.create_mcp_api_key.]"
    )

    def handle(self, *args: Any, **options: Any) -> None:  # type: ignore[override]
        """Emit a deprecation warning and delegate to the canonical command."""

        self.stderr.write(
            self.style.WARNING(
                "Deprecation warning: apps.core.management.commands.create_mcp_api_key "
                "is a compatibility shim and will be removed in a future release. "
                "Use the canonical command in apps.mcp.management.commands.create_mcp_api_key."
            )
        )
        super().handle(*args, **options)


__all__ = ["Command"]

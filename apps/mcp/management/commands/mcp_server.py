from __future__ import annotations

"""Deprecated compatibility wrapper for ``manage.py mcp_server``."""

from typing import Any

from django.core.management.base import BaseCommand

from apps.mcp.management.commands._mcp_command_logic import run_mcp_server


class Command(BaseCommand):
    """Backward-compatible shim for the legacy MCP server command."""

    help = (
        "[Deprecated] Run the suite MCP server over stdio. "
        "Prefer: python manage.py mcp server"
    )

    def add_arguments(self, parser) -> None:
        """Register legacy options for backwards compatibility."""

        parser.add_argument(
            "--allow",
            default="",
            help="Comma-separated allow-list of MCP tool names.",
        )
        parser.add_argument(
            "--deny",
            default="",
            help="Comma-separated deny-list of MCP tool names.",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # type: ignore[override]
        """Emit a deprecation warning and run the canonical MCP server logic."""

        self.stderr.write(
            self.style.WARNING(
                "Deprecation warning: 'python manage.py mcp_server' will be removed in a future release. "
                "Use 'python manage.py mcp server'."
            )
        )
        run_mcp_server(allow_raw=options.get("allow"), deny_raw=options.get("deny"))

from __future__ import annotations

"""Deprecated compatibility wrapper for ``manage.py create_mcp_api_key``."""

from typing import Any

from django.core.management.base import BaseCommand

from apps.mcp.management.commands._mcp_command_logic import create_mcp_api_key


class Command(BaseCommand):
    """Backward-compatible shim for legacy MCP API key creation."""

    help = (
        "[Deprecated] Create an MCP API key for a user. "
        "Prefer: python manage.py mcp key create"
    )

    def add_arguments(self, parser) -> None:
        """Register legacy options for backwards compatibility."""

        parser.add_argument(
            "--username",
            required=True,
            help="Username that will own the generated API key.",
        )
        parser.add_argument(
            "--label",
            default="default",
            help="Human-friendly label used to identify this key.",
        )
        parser.add_argument(
            "--expires-in-days",
            type=int,
            default=90,
            help="Optional expiration in days. Use 0 to create a non-expiring key.",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # type: ignore[override]
        """Emit a deprecation warning and run canonical key-creation logic."""

        self.stderr.write(
            self.style.WARNING(
                "Deprecation warning: 'python manage.py create_mcp_api_key' will be removed in a future "
                "release. Use 'python manage.py mcp key create'."
            )
        )

        username, label, expires_at_text, plain_key = create_mcp_api_key(
            username=options["username"],
            label=options["label"],
            expires_in_days=options["expires_in_days"],
        )
        self.stdout.write(self.style.SUCCESS("MCP API key created."))
        self.stdout.write(f"username={username}")
        self.stdout.write(f"label={label}")
        self.stdout.write(f"expires_at={expires_at_text}")
        self.stdout.write(f"api_key={plain_key}")

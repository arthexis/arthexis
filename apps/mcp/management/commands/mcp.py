from __future__ import annotations

"""Unified MCP management command with discoverable subcommands."""

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.mcp.management.commands._mcp_command_logic import create_mcp_api_key, run_mcp_server


class Command(BaseCommand):
    """Manage MCP server and API keys from a single command namespace."""

    help = (
        "Manage MCP operations. Use 'python manage.py mcp server' to run the MCP server "
        "and 'python manage.py mcp key create' to issue API keys."
    )

    def add_arguments(self, parser) -> None:
        """Register grouped MCP subcommands."""

        subparsers = parser.add_subparsers(dest="group", required=True)

        server_parser = subparsers.add_parser("server", help="Run the suite MCP server over stdio.")
        server_parser.add_argument(
            "--allow",
            default="",
            help="Comma-separated allow-list of MCP tool names.",
        )
        server_parser.add_argument(
            "--deny",
            default="",
            help="Comma-separated deny-list of MCP tool names.",
        )

        key_parser = subparsers.add_parser("key", help="Manage MCP API keys.")
        key_subparsers = key_parser.add_subparsers(dest="key_action", required=True)

        key_create_parser = key_subparsers.add_parser("create", help="Create an MCP API key for a user.")
        key_create_parser.add_argument(
            "--username",
            required=True,
            help="Username that will own the generated API key.",
        )
        key_create_parser.add_argument(
            "--label",
            default="default",
            help="Human-friendly label used to identify this key.",
        )
        key_create_parser.add_argument(
            "--expires-in-days",
            type=int,
            default=90,
            help="Optional expiration in days. Use 0 to create a non-expiring key.",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # type: ignore[override]
        """Dispatch MCP operations to the selected subcommand."""

        group = options.get("group")
        if group == "server":
            run_mcp_server(allow_raw=options.get("allow"), deny_raw=options.get("deny"))
            return

        if group == "key":
            key_action = options.get("key_action")
            if key_action != "create":
                raise CommandError("The key group requires an action. Use: mcp key create")

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
            return

        raise CommandError("A command group is required. Use: mcp server | mcp key create")

from __future__ import annotations

"""Run an MCP server that exposes Arthexis operational tools."""

import os
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.mcp.server import run_stdio_server


def _parse_csv_names(raw_value: str | None) -> set[str]:
    """Parse a comma-separated list into normalized non-empty names."""

    if not raw_value:
        return set()
    normalized: set[str] = set()
    for value in raw_value.split(","):
        item = value.strip()
        if item:
            normalized.add(item)
    return normalized


class Command(BaseCommand):
    """Django command entrypoint for the suite MCP server."""

    help = "Run the suite MCP server over stdio."

    def add_arguments(self, parser) -> None:
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
        allow = _parse_csv_names(options.get("allow"))
        deny = _parse_csv_names(options.get("deny"))

        allow_from_env = _parse_csv_names(os.getenv("ARTHEXIS_MCP_TOOLS_ALLOW"))
        deny_from_env = _parse_csv_names(os.getenv("ARTHEXIS_MCP_TOOLS_DENY"))

        allow.update(allow_from_env)
        deny.update(deny_from_env)

        if allow and deny and allow.issubset(deny):
            raise CommandError("The deny-list blocks every allowed MCP tool.")

        run_stdio_server(allow=allow, deny=deny)

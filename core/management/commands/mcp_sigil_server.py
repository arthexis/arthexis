"""Run the MCP sigil resolver as a standalone SSE server."""

from __future__ import annotations

import asyncio
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.mcp.server import SigilResolverServer


class Command(BaseCommand):
    help = "Start the MCP sigil resolver server over SSE."

    def add_arguments(self, parser) -> None:  # pragma: no cover - exercised via handle
        parser.add_argument("--host", help="Override the configured bind host.")
        parser.add_argument("--port", type=int, help="Override the configured port.")
        parser.add_argument(
            "--public",
            action="store_true",
            help="Bind to all interfaces (0.0.0.0) regardless of configuration.",
        )
        parser.add_argument(
            "--no-auth",
            action="store_true",
            help="Disable API key authentication for local experimentation.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        config = dict(getattr(settings, "MCP_SIGIL_SERVER", {}))
        host = options.get("host") or config.get("host", "127.0.0.1")
        if options.get("public"):
            host = "0.0.0.0"

        port = options.get("port", config.get("port", 8800))
        try:
            port = int(port)
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            raise CommandError("Port must be an integer") from exc

        if options.get("no_auth"):
            config["api_keys"] = []

        config.update({"host": host, "port": port})
        server = SigilResolverServer(config)
        fastmcp = server.build_fastmcp()

        self.stdout.write(self.style.SUCCESS(f"Starting MCP sigil resolver on {host}:{port}"))
        try:
            asyncio.run(fastmcp.run_sse_async())
        except KeyboardInterrupt:  # pragma: no cover - manual interrupt
            self.stdout.write(self.style.WARNING("Shutting down MCP sigil resolver"))

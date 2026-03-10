"""Convenient CLI entrypoint for Pyxel viewport commands."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Open a Pyxel viewport or the live-stats overlay from the CLI."""

    help = "Open a Pyxel viewport window from the CLI."

    def add_arguments(self, parser):
        parser.add_argument(
            "viewport",
            nargs="?",
            help="Pyxel viewport slug or name. Defaults to the default or only viewport.",
        )
        parser.add_argument(
            "--live-stats",
            action="store_true",
            help="Open the live Pyxel stats overlay instead of a saved viewport.",
        )

    def handle(self, *args, **options):
        viewport = str(options.get("viewport") or "").strip()
        live_stats = bool(options.get("live_stats"))

        if live_stats and viewport:
            raise CommandError("Choose either a viewport identifier or --live-stats, not both.")

        if live_stats:
            call_command("live_stats_viewport")
            return

        command_args: list[str] = []
        if viewport:
            command_args.append(viewport)
        call_command("viewport", *command_args)

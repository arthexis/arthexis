"""Synchronize desktop .desktop launchers from DesktopShortcut model data."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.desktop.services import read_backend_port, sync_desktop_shortcuts


class Command(BaseCommand):
    """Render and install desktop launchers for the configured local user."""

    help = "Synchronize desktop shortcuts from desktop models."

    def add_arguments(self, parser) -> None:
        """Declare command-line options for shortcut synchronization."""

        parser.add_argument("--base-dir", default=str(settings.BASE_DIR))
        parser.add_argument("--username", default="")
        parser.add_argument("--port", type=int, default=0)
        parser.add_argument("--no-remove-stale", action="store_true")

    def handle(self, *args, **options):
        """Perform the desktop shortcut synchronization operation."""

        base_dir = Path(options["base_dir"]).resolve()
        username = options["username"].strip()
        if not username:
            try:
                username = base_dir.parts[2]
            except IndexError as exc:
                raise CommandError(
                    "Unable to infer username from base-dir path."
                ) from exc

        port = options["port"]
        if port <= 0:
            port = read_backend_port(base_dir, fallback=8888)

        result = sync_desktop_shortcuts(
            base_dir=base_dir,
            username=username,
            port=port,
            remove_stale=not options["no_remove_stale"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Desktop shortcuts synced: "
                f"installed={result.installed}, skipped={result.skipped}, removed={result.removed}"
            )
        )

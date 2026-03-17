"""Synchronize desktop .desktop launchers from DesktopShortcut model data."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.desktop.services import sync_desktop_shortcuts


def _read_port_from_lock(base_dir: Path, fallback: int = 8888) -> int:
    """Read backend port from ``.locks/backend_port.lck`` when available."""

    lock_file = base_dir / ".locks" / "backend_port.lck"
    try:
        raw_value = lock_file.read_text(encoding="utf-8").strip()
    except OSError:
        return fallback

    if raw_value.isdigit():
        port = int(raw_value)
        if 1 <= port <= 65535:
            return port
    return fallback


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
                raise CommandError("Unable to infer username from base-dir path.") from exc

        port = options["port"]
        if port <= 0:
            port = _read_port_from_lock(base_dir)

        result = sync_desktop_shortcuts(
            base_dir=base_dir,
            username=username,
            port=port,
            remove_stale=not options["no_remove_stale"],
        )
        if result.skipped_db_unavailable:
            raise CommandError(
                "Desktop shortcut sync skipped because the database is unavailable or not migrated yet."
            )
        self.stdout.write(
            self.style.SUCCESS(
                "Desktop shortcuts synced: "
                f"installed={result.installed}, skipped={result.skipped}, removed={result.removed}"
            )
        )

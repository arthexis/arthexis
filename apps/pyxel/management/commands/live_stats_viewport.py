"""Open a local Pyxel window with live suite statistics."""

from __future__ import annotations

import os

from django.core.management.base import BaseCommand, CommandError

from apps.core.ui import build_graphical_subprocess_env, has_graphical_display
from apps.pyxel.live_stats import collect_suite_stats

class Command(BaseCommand):
    """Render live suite metrics into a local Pyxel window."""

    help = "Open a local Pyxel live-stats dashboard window."

    def handle(self, *args, **options):
        """Start the Pyxel render loop and keep it running until the window closes."""

        if not has_graphical_display():
            raise CommandError(
                "No graphical display is configured for this shell. In WSL, ensure WSLg/X11 is available."
            )

        os.environ.update(build_graphical_subprocess_env())

        try:
            import pyxel
        except ImportError as exc:
            raise CommandError("Pyxel library is required for the live stats viewport") from exc

        width, height = 240, 160
        pyxel.init(width, height, title="Arthexis Live Stats", fps=4)

        def _update() -> None:
            return None

        def _draw() -> None:
            stats = collect_suite_stats()
            pyxel.cls(0)
            pyxel.text(8, 8, "ARTHEXIS SUITE", 11)
            pyxel.text(8, 20, f"Users: {stats.users}", 7)
            pyxel.text(8, 32, f"Active sessions: {stats.active_sessions}", 7)
            pyxel.text(8, 44, f"Installed apps: {stats.installed_apps}", 7)
            pyxel.text(8, 56, f"Registered models: {stats.registered_models}", 7)
            pyxel.text(8, 78, "Updated", 6)
            pyxel.text(60, 78, stats.timestamp, 10)
            pyxel.text(8, 146, "Close this window to stop", 5)

        try:
            pyxel.run(_update, _draw)
        except RuntimeError as exc:
            raise CommandError(f"Unable to run Pyxel live stats viewport: {exc}") from exc

"""Print resolved desktop-launch capabilities from node feature state."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.desktop.services import serialize_desktop_launch_capabilities


class Command(BaseCommand):
    """Output canonical desktop-launch capability metadata as JSON."""

    help = "Print desktop-launch capabilities resolved from node feature state as JSON."

    def add_arguments(self, parser) -> None:
        """Declare command options for capability resolution."""

        parser.add_argument("--base-dir", default=str(settings.BASE_DIR))

    def handle(self, *args, **options):
        """Resolve and print desktop-launch capabilities."""

        base_dir = Path(options["base_dir"]).resolve()
        self.stdout.write(serialize_desktop_launch_capabilities(base_dir=base_dir))

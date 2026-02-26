from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Backward-compatible wrapper for snapshot capture."""

    help = "Capture a snapshot from the default camera. Deprecated: use `video snapshot`."

    def handle(self, *args, **options) -> None:
        """Delegate snapshot handling to the unified ``video`` command."""

        self.stdout.write(self.style.WARNING("`snapshot` is deprecated; use `video snapshot`."))
        call_command("video", "snapshot", auto_enable=True, discover=True)

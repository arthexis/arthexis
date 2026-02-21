from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Backward-compatible wrapper for snapshot capture."""

    help = "Capture a snapshot from the default camera and print the file path."

    def handle(self, *args, **options) -> str:
        """Delegate snapshot handling to the unified ``video`` command."""

        return call_command("video", snapshot=True, auto_enable=True)

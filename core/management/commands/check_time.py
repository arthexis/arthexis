from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    """Print the current server time."""

    help = "Display the current server time."

    def handle(self, *args, **options):
        current_time = timezone.localtime()
        self.stdout.write(
            self.style.SUCCESS(f"Current server time: {current_time.isoformat()}")
        )

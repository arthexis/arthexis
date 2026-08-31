from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from utils.loggers.config import resolve_log_formatter
from utils.loggers.paths import select_log_dir


class Command(BaseCommand):
    help = "Show the active Arthexis logging profile."

    def handle(self, *args, **options):
        """Print active logging formatter and log directory."""

        formatter_mode = resolve_log_formatter()
        log_dir = getattr(settings, "LOG_DIR", None)
        configured_log_dir = (
            Path(log_dir) if log_dir else select_log_dir(Path(settings.BASE_DIR))
        )

        self.stdout.write("Logging profile")
        self.stdout.write(f"- formatter: {formatter_mode}")
        self.stdout.write(f"- log_dir: {configured_log_dir}")

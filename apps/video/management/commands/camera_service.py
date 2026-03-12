from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Backward-compatible wrapper for camera service mode."""

    help = "Run camera service. Legacy alias: use `video service`."

    def add_arguments(self, parser) -> None:
        """Register camera service compatibility arguments."""

        parser.add_argument(
            "--interval",
            type=float,
            help="Seconds between frame capture attempts per stream.",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            help="Seconds to sleep between capture loops.",
        )

    def handle(self, *args, **options) -> None:
        """Delegate camera service execution to ``video service``."""

        self.stdout.write(self.style.WARNING("`camera_service` is a legacy alias; use `video service`."))

        kwargs: dict[str, float] = {
            key: value
            for key, value in options.items()
            if key in ("interval", "sleep") and value is not None
        }

        call_command("video", "service", **kwargs)

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Backward-compatible wrapper for camera service mode."""

    help = "Run camera service. Deprecated: use `video service`."

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

        self.stdout.write(self.style.WARNING("`camera_service` is deprecated; use `video service`."))

        kwargs: dict[str, float] = {}
        if options.get("interval") is not None:
            kwargs["interval"] = float(options["interval"])
        if options.get("sleep") is not None:
            kwargs["sleep"] = float(options["sleep"])

        call_command("video", "service", **kwargs)

from __future__ import annotations

import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.content.utils import capture_and_save_screenshot


class Command(BaseCommand):
    """Capture a screenshot and record it as a :class:`ContentSample`."""

    help = "Capture a screenshot, save it as content, and print the file path."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "url",
            nargs="?",
            help="URL to capture. Defaults to the local node site (localhost:8888).",
        )
        parser.add_argument(
            "--freq",
            type=int,
            help="Capture another screenshot every N seconds until stopped.",
        )
        parser.add_argument(
            "--local",
            action="store_true",
            help="Capture a screenshot of the local desktop instead of a URL.",
        )

    def handle(self, *args, **options) -> str:
        frequency = options.get("freq")
        if frequency is not None and frequency <= 0:
            raise CommandError("--freq must be a positive integer")

        local_capture = options.get("local")
        url: str | None = options.get("url")

        if local_capture and url:
            raise CommandError("--local cannot be used together with a URL")

        last_path: Path | None = None

        try:
            while True:
                path = capture_and_save_screenshot(
                    url=url,
                    method="COMMAND",
                    local=local_capture,
                )
                path_str = path.as_posix() if path else ""
                self.stdout.write(path_str)
                last_path = path
                if frequency is None:
                    break
                time.sleep(frequency)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Stopping screenshot capture"))

        return last_path.as_posix() if last_path else ""

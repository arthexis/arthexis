from __future__ import annotations

import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.nodes.models import Node
from apps.nodes.utils import capture_local_screenshot, capture_screenshot, save_screenshot


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

        if not local_capture:
            url = url or self._default_url()
        node = Node.get_local()
        last_path: Path | None = None

        capture = capture_local_screenshot if local_capture else capture_screenshot

        try:
            while True:
                path = capture() if local_capture else capture(url)
                save_screenshot(path, node=node, method="COMMAND")
                self.stdout.write(str(path))
                last_path = path
                if frequency is None:
                    break
                time.sleep(frequency)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Stopping screenshot capture"))

        return str(last_path) if last_path else ""

    def _default_url(self, port: int = 8888) -> str:
        node = Node.get_local()
        scheme = node.get_preferred_scheme() if node else "http"
        return f"{scheme}://localhost:{port}"

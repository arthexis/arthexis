from __future__ import annotations

import hashlib
import json
from collections import deque
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.tasks import report_github_issue


class Command(BaseCommand):
    """Queue an asynchronous GitHub issue report.

    The Celery worker uses the :envvar:`GITHUB_TOKEN` environment variable to
    authenticate with GitHub, so administrators should provide a personal
    access token there when they want automated tickets to be created.
    """

    help = "Queue a GitHub issue describing a local failure."

    def add_arguments(self, parser) -> None:  # type: ignore[override]
        parser.add_argument("--source", required=True, help="Origin of the failure (e.g. start)")
        parser.add_argument("--command", required=True, help="Command that failed")
        parser.add_argument("--exit-code", required=True, type=int, dest="exit_code")
        parser.add_argument("--host", required=True, help="Host reporting the failure")
        parser.add_argument(
            "--app-version",
            required=True,
            dest="app_version",
            help="Current application version",
        )
        parser.add_argument("--revision", required=True, help="Current git revision")
        parser.add_argument(
            "--log-file",
            dest="log_file",
            help="Path to the log file to include in the report",
        )
        parser.add_argument(
            "--max-log-lines",
            dest="max_log_lines",
            type=int,
            default=100,
            help="Number of log lines to capture from the tail of the file",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # type: ignore[override]
        log_excerpt = self._read_log_excerpt(options.get("log_file"), options["max_log_lines"])
        payload: dict[str, Any] = {
            "source": options["source"],
            "command": options["command"],
            "exit_code": options["exit_code"],
            "host": options["host"],
            "version": options["app_version"],
            "revision": options["revision"],
            "log_excerpt": log_excerpt,
            "captured_at": timezone.now().isoformat(),
        }

        payload["fingerprint"] = self._compute_fingerprint(payload)

        report_github_issue.delay(payload)
        self.stdout.write(self.style.SUCCESS(f"Queued failure report {payload['fingerprint'][:12]}"))

    @staticmethod
    def _read_log_excerpt(log_file: str | None, max_lines: int) -> str:
        if not log_file:
            return ""

        path = Path(log_file)
        if not path.exists():
            return ""

        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                lines = deque(handle, maxlen=max_lines)
        except OSError:  # pragma: no cover - filesystem errors are rare
            return ""

        return "\n".join(line.rstrip("\n") for line in lines)

    @staticmethod
    def _compute_fingerprint(payload: dict[str, Any]) -> str:
        fingerprint_source = json.dumps(
            {
                "command": payload.get("command"),
                "exit_code": payload.get("exit_code"),
                "revision": payload.get("revision"),
                "source": payload.get("source"),
                "version": payload.get("version"),
            },
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(fingerprint_source).hexdigest()

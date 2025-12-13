from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.core import system


class Command(BaseCommand):
    """Display startup activity captured by suite lifecycle scripts."""

    help = (
        "Show recent start.sh and upgrade.sh lifecycle entries from the startup report log."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--n",
            type=int,
            dest="limit",
            default=10,
            help=(
                "Number of entries to display from the startup report log "
                "(default: 10)."
            ),
        )

    def handle(self, *args, **options):  # noqa: D401 - inherited docstring
        limit = options.get("limit") or 10
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 10
        if limit < 1:
            limit = 10

        report = system._read_startup_report(
            limit=limit, base_dir=Path(settings.BASE_DIR)
        )
        log_path = report.get("log_path")
        if log_path:
            self.stdout.write(f"Startup report log: {log_path}")

        clock_warning = report.get("clock_warning")
        if clock_warning:
            self.stderr.write(str(clock_warning))

        error = report.get("error")
        if error:
            self.stderr.write(str(error))
            return

        entries = report.get("entries", [])
        if not entries:
            self.stdout.write("No startup activity has been recorded yet.")
            return

        for entry in entries:
            timestamp = entry.get("timestamp_label") or entry.get("timestamp_raw") or ""
            script = entry.get("script") or "unknown"
            event = entry.get("event") or "event"
            detail = entry.get("detail") or ""

            message = f"{timestamp} [{script}] {event}"
            if detail:
                message = f"{message} — {detail}"
            self.stdout.write(message)

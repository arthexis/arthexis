from __future__ import annotations

import textwrap
from typing import Iterable, List

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.sites.models import ViewHistory


class Command(BaseCommand):
    help = "Display recent view errors."

    def add_arguments(self, parser):
        parser.add_argument(
            "--last",
            type=int,
            default=5,
            help="Number of recent errors to show (ignored when using --all)",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Show all recorded errors",
        )

    def handle(self, *args, **options):
        limit = options.get("last")
        show_all = options.get("all")

        if not show_all and (limit is None or limit <= 0):
            raise CommandError("--last must be a positive integer")

        queryset = ViewHistory.objects.filter(status_code__gte=400).order_by(
            "-visited_at"
        )
        if not show_all:
            queryset = queryset[:limit]

        entries = list(queryset)
        if not entries:
            self.stdout.write(self.style.WARNING("No errors found."))
            return

        headers = ["Path", "Status", "Time", "Exception"]
        rows = [self._format_row(entry) for entry in entries]
        table = self._render_table(headers, rows)
        self.stdout.write(table)

    def _format_row(self, entry: ViewHistory) -> List[str]:
        path = textwrap.shorten(entry.path, width=60, placeholder="…")
        status = str(entry.status_code)
        timestamp = timezone.localtime(entry.visited_at).strftime("%Y-%m-%d %H:%M:%S")
        exception_name = entry.exception_name or "-"
        return [path, status, timestamp, exception_name]

    def _render_table(self, headers: List[str], rows: Iterable[List[str]]) -> str:
        widths = [len(header) for header in headers]
        for row in rows:
            for index, value in enumerate(row):
                widths[index] = max(widths[index], len(value))

        def format_row(values: List[str]) -> str:
            return " | ".join(value.ljust(widths[idx]) for idx, value in enumerate(values))

        separator = "-+-".join("-" * width for width in widths)
        lines = [format_row(headers), separator]
        lines.extend(format_row(row) for row in rows)
        return "\n".join(lines)

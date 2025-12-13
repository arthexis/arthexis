from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.ocpp.models import RFIDSessionAttempt


class Command(BaseCommand):
    help = (
        "Display the most recent RFID values that attempted to start an OCPP session."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--last",
            dest="count",
            type=int,
            default=10,
            help="Number of attempts to show (default: 10).",
        )

    def handle(self, *args, **options):
        count = options["count"]
        if count <= 0:
            raise CommandError("--last must be a positive integer")

        attempts = (
            RFIDSessionAttempt.objects.select_related("account")
            .order_by("-attempted_at")[:count]
        )
        if not attempts:
            self.stdout.write("No RFID session attempts recorded.")
            return

        headers = ["Date", "RFID", "Status", "Account"]
        rows: list[tuple[str, str, str, str]] = []
        for attempt in attempts:
            timestamp = attempt.attempted_at
            if timezone.is_naive(timestamp):
                display_time = timestamp.strftime("%Y-%m-%d %H:%M:%S")
            else:
                localized = timezone.localtime(timestamp)
                display_time = localized.strftime("%Y-%m-%d %H:%M:%S %Z")
            rows.append(
                (
                    display_time,
                    attempt.rfid or "-",
                    attempt.get_status_display() or attempt.status,
                    attempt.account.name if attempt.account else "-",
                )
            )

        widths = [
            max(len(header), *(len(row[idx]) for row in rows))
            for idx, header in enumerate(headers)
        ]
        header_line = "  ".join(
            header.ljust(widths[idx]) for idx, header in enumerate(headers)
        )
        self.stdout.write(header_line)
        for row in rows:
            self.stdout.write(
                "  ".join(row[idx].ljust(widths[idx]) for idx in range(len(headers)))
            )

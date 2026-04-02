from django.core.management.base import BaseCommand

from apps.sites.maintenance import coerce_retention_days, purge_view_history


class Command(BaseCommand):
    """Purge stale view history entries."""

    help = "Purge stale ViewHistory entries older than a configurable age in days."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            dest="days",
            type=int,
            default=15,
            help="Delete entries older than this many days (default: 15).",
        )

    def handle(self, *args, **options):
        days = coerce_retention_days(options["days"])
        deleted = purge_view_history(days=days)
        self.stdout.write(f"Purged {deleted} view history entries older than {days} days.")

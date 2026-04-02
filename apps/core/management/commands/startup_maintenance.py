"""Run startup maintenance hooks that are intentionally outside AppConfig.ready()."""

from django.core.management.base import BaseCommand

from apps.ocpp.maintenance import reset_cached_statuses
from apps.sites.maintenance import purge_view_history


class Command(BaseCommand):
    """Run startup-oriented operational cleanup tasks."""

    help = "Run startup-oriented maintenance cleanups from app-owned modules."

    def add_arguments(self, parser):
        parser.add_argument(
            "--view-history-days",
            type=int,
            default=15,
            help="Delete view history entries older than this many days (default: 15).",
        )

    def handle(self, *args, **options):
        days = max(1, options["view_history_days"])

        cleared = reset_cached_statuses()
        self.stdout.write(f"OCPP cached statuses cleared: {cleared}")

        deleted = purge_view_history(days=days)
        self.stdout.write(
            f"Site view history entries purged (older than {days} days): {deleted}"
        )

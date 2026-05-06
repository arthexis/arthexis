"""Run startup maintenance hooks that are intentionally outside AppConfig.ready()."""

from django.core.management.base import BaseCommand

from apps.ocpp.maintenance import reset_cached_statuses
from apps.sites.maintenance import coerce_retention_days, purge_view_history
from apps.skills.agent_context import write_agents_context


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
        days = coerce_retention_days(options["view_history_days"])

        cleared = reset_cached_statuses()
        self.stdout.write(f"OCPP cached statuses cleared: {cleared}")

        deleted = purge_view_history(days=days)
        self.stdout.write(
            f"Site view history entries purged (older than {days} days): {deleted}"
        )

        agents_result = write_agents_context()
        status = "written" if agents_result.written else "unchanged"
        self.stdout.write(f"Local AGENTS context {status}: {agents_result.path}")

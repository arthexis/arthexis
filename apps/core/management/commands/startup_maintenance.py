"""Run startup maintenance hooks that are intentionally outside AppConfig.ready()."""

from django.core.management.base import BaseCommand

from apps.core.services.profile_apps import profile_skip_reason


def _reset_cached_statuses():
    from apps.ocpp.maintenance import reset_cached_statuses

    return reset_cached_statuses()


def _coerce_view_history_days(days: int) -> int:
    from apps.sites.maintenance import coerce_retention_days

    return coerce_retention_days(days)


def _purge_view_history(*, days: int) -> int:
    from apps.sites.maintenance import purge_view_history

    return purge_view_history(days=days)


def _write_agents_context():
    from apps.skills.agent_context import write_agents_context

    return write_agents_context()


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
        if skip_reason := profile_skip_reason(app_selector="apps.ocpp"):
            self.stdout.write(f"OCPP cached statuses skipped: {skip_reason}")
        else:
            cleared = _reset_cached_statuses()
            self.stdout.write(f"OCPP cached statuses cleared: {cleared}")

        if skip_reason := profile_skip_reason(app_selector="apps.sites"):
            self.stdout.write(f"Site view history purge skipped: {skip_reason}")
        else:
            days = _coerce_view_history_days(options["view_history_days"])
            deleted = _purge_view_history(days=days)
            self.stdout.write(
                f"Site view history entries purged (older than {days} days): {deleted}"
            )

        if skip_reason := profile_skip_reason(app_selector="apps.skills"):
            self.stdout.write(f"Local AGENTS context skipped: {skip_reason}")
        else:
            agents_result = _write_agents_context()
            status = "written" if agents_result.written else "unchanged"
            self.stdout.write(f"Local AGENTS context {status}: {agents_result.path}")

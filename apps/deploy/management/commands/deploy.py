"""Operational deployment status command for the Deploy app."""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.utils import OperationalError, ProgrammingError

from apps.deploy.models import DeployInstance, DeployRun, DeployServer


class Command(BaseCommand):
    """Show deploy status information."""

    help = "Show deployment status for configured deploy instances."

    def add_arguments(self, parser):
        """Register command options."""

        parser.add_argument(
            "--limit",
            type=int,
            default=10,
            help="How many recent runs to show for status mode (default: 10).",
        )

    def handle(self, *args, **options):
        """Render deployment status output."""
        self._handle_status(limit=max(1, int(options["limit"])))

    def _handle_status(self, *, limit: int) -> None:
        """Render deployment summary information for operators."""

        try:
            instances = list(
                DeployInstance.objects.select_related("server").order_by("server__name", "name")
            )
        except (OperationalError, ProgrammingError):
            self.stdout.write("Deployment tables are not available yet.")
            self.stdout.write("Run migrations before using the deploy command.")
            return

        if not instances:
            self.stdout.write("No deployment instances configured yet.")
            self.stdout.write("Use admin Deploy models to register servers and instances.")
            return

        self.stdout.write("Configured deployment instances:")
        for instance in instances:
            status = "enabled" if instance.is_enabled else "disabled"
            self.stdout.write(
                f"- {instance.server.name}:{instance.name} [{status}] "
                f"service={instance.service_name} dir={instance.install_dir}"
            )

        recent_runs = list(
            DeployRun.objects.select_related("instance", "instance__server", "release")[:limit]
        )
        self.stdout.write("")
        self.stdout.write(f"Recent deploy runs (latest {limit}):")
        if not recent_runs:
            self.stdout.write("- No deployment runs recorded yet.")
            return

        for run in recent_runs:
            release = run.release.version if run.release else "-"
            self.stdout.write(
                f"- #{run.pk} {run.instance.server.name}:{run.instance.name} "
                f"action={run.action} status={run.status} release={release}"
            )

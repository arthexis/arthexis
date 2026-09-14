from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.core.system.lifecycle_status import inspect_lifecycle, render_lifecycle_status


class Command(BaseCommand):
    """Report lifecycle ownership, deployment state, and application health."""

    help = "Show Arthexis lifecycle ownership, managed state, and health diagnostics"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--json",
            action="store_true",
            help="Render the lifecycle report as JSON.",
        )

    def handle(self, *args, **options):
        report = inspect_lifecycle()
        if options["json"]:
            self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        else:
            self.stdout.write(render_lifecycle_status(report))

        if report["state"] in {"invalid", "degraded"}:
            raise SystemExit(1)

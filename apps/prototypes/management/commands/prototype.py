"""Inspect retired prototype records and clear legacy runtime overlays."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.prototypes import prototype_ops
from apps.prototypes.models import Prototype
from apps.prototypes.prototype_ops import RETIREMENT_MESSAGE


_RETIRED_MUTATING_ACTIONS = {"create", "activate"}


class Command(BaseCommand):
    """Report retired prototype metadata and clear legacy runtime overlays."""

    help = (
        "Inspect retired prototype metadata. Runtime scaffold generation is no longer "
        "supported, but legacy deactivate cleanup remains available."
    )

    def add_arguments(self, parser):
        """Register command arguments.

        Parameters:
            parser: Django's argument parser.

        Returns:
            None.
        """

        parser.add_argument(
            "action",
            nargs="?",
            default="status",
            choices=sorted(_RETIRED_MUTATING_ACTIONS | {"deactivate", "status"}),
            help="Legacy action name. Only 'status' and legacy cleanup via 'deactivate' remain supported.",
        )
        parser.add_argument(
            "--no-restart",
            action="store_true",
            help="Only clear legacy env and lock state; do not restart the suite.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Pass --force to stop.sh when restarting after cleanup.",
        )

    def handle(self, *args, **options):
        """Dispatch the selected command action.

        Parameters:
            *args: Positional arguments supplied by Django.
            **options: Parsed command options.

        Returns:
            None.

        Raised exceptions:
            CommandError: Raised when a retired mutating action is requested.
        """

        action = options["action"]
        if action in _RETIRED_MUTATING_ACTIONS:
            raise CommandError(RETIREMENT_MESSAGE)
        if action == "deactivate":
            prototype_ops.clear_legacy_runtime_state()
            self.stdout.write(self.style.SUCCESS("Cleared legacy prototype runtime state."))
            if options["no_restart"]:
                self.stdout.write("Restart skipped.")
                return
            prototype_ops.restart_suite(force_stop=bool(options["force"]))
            self.stdout.write(self.style.SUCCESS("Suite restarted without a prototype overlay."))
            return

        self.stdout.write(RETIREMENT_MESSAGE)
        rows = list(Prototype.objects.order_by("name", "slug"))
        if not rows:
            self.stdout.write("No prototype records stored.")
            return

        for prototype in rows:
            retired_at = prototype.retired_at.isoformat() if prototype.retired_at else "unknown"
            self.stdout.write(
                f"- {prototype.slug} | runnable={prototype.is_runnable} | retired_at={retired_at}"
            )

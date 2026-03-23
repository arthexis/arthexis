"""Inspect retired prototype records without mutating runtime state."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.prototypes.models import Prototype
from apps.prototypes.prototype_ops import RETIREMENT_MESSAGE


_MUTATING_ACTIONS = {"create", "activate", "deactivate"}


class Command(BaseCommand):
    """Report retired prototype metadata and block legacy runtime operations."""

    help = "Inspect retired prototype metadata. Runtime scaffold generation is no longer supported."

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
            choices=sorted(_MUTATING_ACTIONS | {"status"}),
            help="Legacy action name. Only 'status' is still supported.",
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
        if action in _MUTATING_ACTIONS:
            raise CommandError(RETIREMENT_MESSAGE)

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

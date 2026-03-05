"""Backwards-compatible alias for ``release run-data-transforms``."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Delegate legacy entrypoint to ``release run-data-transforms``."""

    help = "Alias for `release run-data-transforms` (kept for compatibility)."

    def add_arguments(self, parser):
        parser.add_argument(
            "transform",
            nargs="?",
            help="Optional transform name. Runs all registered transforms when omitted.",
        )
        parser.add_argument(
            "--max-batches",
            type=int,
            default=1,
            help="Number of batches to process for each transform.",
        )

    def handle(self, *args, **options):
        command_args = ["release", "run-data-transforms"]
        transform = options.get("transform")
        if transform:
            command_args.append(transform)

        call_command(
            *command_args,
            max_batches=int(options.get("max_batches", 1)),
            stdout=self.stdout,
            stderr=self.stderr,
        )

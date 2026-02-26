"""Run deferred idempotent data transforms used by package release updates."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.release.domain import list_transform_names, run_transform


class Command(BaseCommand):
    """Execute deferred release transforms with progress checkpoints."""

    help = (
        "Run idempotent, checkpointed data transforms that were intentionally moved "
        "out of schema-critical migrations."
    )

    def add_arguments(self, parser) -> None:
        """Register command arguments."""

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

    def handle(self, *args, **options) -> None:
        """Run requested transforms and print per-batch progress."""

        max_batches = options["max_batches"]
        if max_batches < 1:
            raise CommandError("--max-batches must be >= 1")

        transform_name = options.get("transform")
        if transform_name:
            names = [str(transform_name)]
        else:
            names = list_transform_names()

        for name in names:
            self._run_transform_batches(name, max_batches=max_batches)

    def _run_transform_batches(self, transform_name: str, *, max_batches: int) -> None:
        """Run one transform for up to ``max_batches`` iterations."""

        for index in range(max_batches):
            try:
                result = run_transform(transform_name)
            except KeyError as exc:
                raise CommandError(str(exc)) from exc

            self.stdout.write(
                f"{transform_name}: batch={index + 1} processed={result.processed} "
                f"updated={result.updated} complete={result.complete}"
            )
            if result.complete:
                break

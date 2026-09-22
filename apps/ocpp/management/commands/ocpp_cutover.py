"""Inspect or change the Arthexis authority cutover for one charger."""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.ocpp.models import Charger


class Command(BaseCommand):
    help = "Show, set, or clear one charger's Arthexis authority cutover timestamp."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "charger",
            metavar="IDENTITY",
            help="Exact charger identity to inspect or update.",
        )
        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            "--set",
            dest="cutover",
            metavar="ISO_TIMESTAMP",
            help=(
                "Set the authority cutover to an offset-aware ISO-8601 timestamp. "
                "Evidence strictly before it is historical."
            ),
        )
        group.add_argument(
            "--clear",
            action="store_true",
            help="Clear the explicit cutover and preserve normal live semantics.",
        )

    def handle(self, *args, **options) -> None:
        try:
            charger = Charger.objects.get(identity=options["charger"])
        except Charger.DoesNotExist as error:
            raise CommandError(f"Unknown charger: {options['charger']}") from error

        cutover = options.get("cutover")
        if cutover is not None:
            parsed = parse_datetime(cutover)
            if parsed is None:
                raise CommandError("Cutover must be a valid ISO-8601 timestamp.")
            if timezone.is_naive(parsed):
                raise CommandError(
                    "Cutover must include a timezone offset or Z suffix."
                )
            charger.authority_cutover_at = parsed
            charger.save(update_fields=("authority_cutover_at",))
        elif options.get("clear"):
            charger.authority_cutover_at = None
            charger.save(update_fields=("authority_cutover_at",))

        value = charger.authority_cutover_at
        rendered = value.isoformat() if value is not None else "unset"
        self.stdout.write(f"{charger.identity}: authority cutover {rendered}")

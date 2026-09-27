"""Inspect or change OCPP charger and RFID admission policy."""

from django.core.management.base import BaseCommand, CommandError

from apps.ocpp.models import Charger, OcppPolicy


class Command(BaseCommand):
    help = "Inspect or change instance charger admission and per-charger RFID policy."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--charger-admission",
            choices=tuple(OcppPolicy.AdmissionMode.values),
            help="Set instance admission for new/existing chargers.",
        )
        parser.add_argument(
            "--charger",
            metavar="IDENTITY",
            help="Select one charger whose RFID policy should be inspected or changed.",
        )
        parser.add_argument(
            "--rfid",
            choices=tuple(Charger.AuthorizationMode.values),
            help="Set RFID authorization for the selected charger.",
        )

    def handle(self, *args, **options) -> None:
        if options["rfid"] and not options["charger"]:
            raise CommandError("--rfid requires --charger.")

        policy = OcppPolicy.load()
        if options["charger_admission"]:
            policy.charger_admission_mode = options["charger_admission"]
            policy.save(update_fields=("charger_admission_mode", "updated_at"))

        self.stdout.write(
            f"charger_admission={policy.charger_admission_mode}"
        )

        identity = options["charger"]
        if not identity:
            return

        try:
            charger = Charger.objects.get(identity=identity)
        except Charger.DoesNotExist as error:
            raise CommandError(f"Unknown charger: {identity}") from error

        if options["rfid"]:
            charger.authorization_mode = options["rfid"]
            charger.save(update_fields=("authorization_mode",))

        self.stdout.write(
            f"charger={charger.identity} rfid={charger.authorization_mode}"
        )

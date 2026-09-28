"""Inspect or change OCPP admission, protocol, and card policy."""

from django.core.management.base import BaseCommand, CommandError

from apps.ocpp.models import Charger, OcppPolicy

PUBLIC_TO_STORED = {
    "permissive": OcppPolicy.AdmissionMode.OPEN,
    "strict": OcppPolicy.AdmissionMode.RESTRICTED,
    # Legacy programmatic values remain accepted.
    "open": OcppPolicy.AdmissionMode.OPEN,
    "restricted": OcppPolicy.AdmissionMode.RESTRICTED,
}
STORED_TO_PUBLIC = {
    OcppPolicy.AdmissionMode.OPEN: "permissive",
    OcppPolicy.AdmissionMode.RESTRICTED: "strict",
}


class Command(BaseCommand):
    help = "Inspect or change OCPP admission, protocol, and card permissiveness."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--admission",
            choices=("permissive", "strict"),
            help="Set instance admission for unknown chargers.",
        )
        parser.add_argument(
            "--charger-admission",
            dest="charger_admission",
            choices=("open", "restricted"),
            help="Legacy alias for --admission.",
        )
        parser.add_argument(
            "--protocol",
            choices=("permissive", "strict"),
            help="Set instance protocol default or selected charger protocol mode.",
        )
        parser.add_argument(
            "--cards",
            choices=("permissive", "strict"),
            help="Set instance card default or selected charger card mode.",
        )
        parser.add_argument(
            "--rfid",
            choices=("open", "restricted"),
            help="Legacy alias for --cards; requires --charger.",
        )
        parser.add_argument(
            "--charger",
            metavar="IDENTITY",
            help="Select one existing charger for protocol/card policy.",
        )

    @staticmethod
    def _coalesce(
        canonical: str | None,
        legacy: str | None,
        *,
        canonical_name: str,
        legacy_name: str,
    ) -> str | None:
        if canonical and legacy:
            raise CommandError(
                f"{canonical_name} and {legacy_name} cannot be used together."
            )
        return canonical or legacy

    def handle(self, *args, **options) -> None:
        admission = self._coalesce(
            options["admission"],
            options["charger_admission"],
            canonical_name="--admission",
            legacy_name="--charger-admission",
        )
        cards = self._coalesce(
            options["cards"],
            options["rfid"],
            canonical_name="--cards",
            legacy_name="--rfid",
        )
        if options["rfid"] and not options["charger"]:
            raise CommandError("--rfid requires --charger.")
        if options["charger"] and admission:
            raise CommandError("--admission is instance-only and cannot use --charger.")

        policy = OcppPolicy.load()
        identity = options["charger"]
        if identity:
            try:
                charger = Charger.objects.get(identity=identity)
            except Charger.DoesNotExist as error:
                raise CommandError(f"Unknown charger: {identity}") from error

            update_fields = []
            if options["protocol"]:
                charger.protocol_mode = PUBLIC_TO_STORED[options["protocol"]]
                update_fields.append("protocol_mode")
            if cards:
                charger.authorization_mode = PUBLIC_TO_STORED[cards]
                update_fields.append("authorization_mode")
            if update_fields:
                charger.save(update_fields=tuple(update_fields))

            self.stdout.write(
                " ".join(
                    (
                        f"charger={charger.identity}",
                        f"protocol={STORED_TO_PUBLIC[charger.protocol_mode]}",
                        f"cards={STORED_TO_PUBLIC[charger.authorization_mode]}",
                    )
                )
            )
            return

        update_fields = []
        if admission:
            policy.charger_admission_mode = PUBLIC_TO_STORED[admission]
            update_fields.append("charger_admission_mode")
        if options["protocol"]:
            policy.protocol_mode = PUBLIC_TO_STORED[options["protocol"]]
            update_fields.append("protocol_mode")
        if cards:
            policy.card_mode = PUBLIC_TO_STORED[cards]
            update_fields.append("card_mode")
        if update_fields:
            update_fields.append("updated_at")
            policy.save(update_fields=tuple(update_fields))

        self.stdout.write(
            " ".join(
                (
                    f"admission={STORED_TO_PUBLIC[policy.charger_admission_mode]}",
                    f"protocol={STORED_TO_PUBLIC[policy.protocol_mode]}",
                    f"cards={STORED_TO_PUBLIC[policy.card_mode]}",
                )
            )
        )

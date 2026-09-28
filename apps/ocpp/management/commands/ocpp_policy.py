"""Inspect or change OCPP admission, protocol, and card policy."""

from django.core.management.base import BaseCommand, CommandError

from apps.ocpp.models import Charger, OcppPolicy

PUBLIC_TO_STORED = {
    "permissive": OcppPolicy.AdmissionMode.OPEN,
    "strict": OcppPolicy.AdmissionMode.RESTRICTED,
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
            "--charger-admission",
            dest="admission",
            choices=tuple(PUBLIC_TO_STORED),
            help="Set instance admission for unknown chargers.",
        )
        parser.add_argument(
            "--protocol",
            choices=tuple(PUBLIC_TO_STORED),
            help="Set instance protocol default or selected charger protocol mode.",
        )
        parser.add_argument(
            "--cards",
            "--rfid",
            dest="cards",
            choices=tuple(PUBLIC_TO_STORED),
            help="Set instance card default or selected charger card mode.",
        )
        parser.add_argument(
            "--charger",
            metavar="IDENTITY",
            help="Select one existing charger for protocol/card policy.",
        )

    def handle(self, *args, **options) -> None:
        if options["charger"] and options["admission"]:
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
            if options["cards"]:
                charger.authorization_mode = PUBLIC_TO_STORED[options["cards"]]
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
        if options["admission"]:
            policy.charger_admission_mode = PUBLIC_TO_STORED[options["admission"]]
            update_fields.append("charger_admission_mode")
        if options["protocol"]:
            policy.protocol_mode = PUBLIC_TO_STORED[options["protocol"]]
            update_fields.append("protocol_mode")
        if options["cards"]:
            policy.card_mode = PUBLIC_TO_STORED[options["cards"]]
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

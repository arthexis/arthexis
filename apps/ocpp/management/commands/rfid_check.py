import json

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from apps.cards.models import RFID
from apps.cards.reader import read_rfid, validate_rfid_value


class Command(BaseCommand):
    help = "Validate an RFID tag by label, UID, or an interactive scan."

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument(
            "--label",
            help="Validate an RFID associated with the given label id or custom label.",
        )
        target.add_argument(
            "--uid",
            help="Validate an RFID by providing the UID value directly.",
        )
        target.add_argument(
            "--scan",
            action="store_true",
            help="Start the RFID scanner and return the first successfully read tag.",
        )

        parser.add_argument(
            "--kind",
            choices=[choice[0] for choice in RFID.KIND_CHOICES],
            help="Optional RFID kind when validating a UID directly.",
        )
        parser.add_argument(
            "--endianness",
            choices=[choice[0] for choice in RFID.ENDIANNESS_CHOICES],
            help="Optional endianness when validating a UID directly.",
        )
        parser.add_argument(
            "--timeout",
            type=float,
            default=5.0,
            help="How long to wait for a scan before timing out (seconds).",
        )
        parser.add_argument(
            "--pretty",
            action="store_true",
            help="Pretty-print the JSON response.",
        )

    def handle(self, *args, **options):
        if options.get("scan"):
            result = self._scan(options)
        elif options.get("label"):
            result = self._validate_label(options["label"])
        else:
            result = self._validate_uid(
                options.get("uid"),
                kind=options.get("kind"),
                endianness=options.get("endianness"),
            )

        if "error" in result:
            raise CommandError(result["error"])

        pretty = options.get("pretty", False)
        dump_kwargs = {"indent": 2, "sort_keys": True} if pretty else {}
        payload = json.dumps(result, **dump_kwargs)
        self.stdout.write(payload)

    def _validate_uid(self, value: str | None, *, kind: str | None, endianness: str | None):
        if not value:
            raise CommandError("RFID UID value is required")

        return validate_rfid_value(value, kind=kind, endianness=endianness)

    def _validate_label(self, label_value: str):
        if label_value is None:
            raise CommandError("Label value is required")

        cleaned = label_value.strip()
        if not cleaned:
            raise CommandError("Label value is required")

        query: Q | None = None
        try:
            label_id = int(cleaned)
        except ValueError:
            label_id = None
        else:
            query = Q(label_id=label_id)

        label_query = Q(custom_label__iexact=cleaned)
        query = label_query if query is None else query | label_query

        tag = RFID.objects.filter(query).order_by("label_id").first()
        if tag is None:
            raise CommandError(f"No RFID found for label '{cleaned}'")

        return validate_rfid_value(tag.rfid, kind=tag.kind, endianness=tag.endianness)

    def _scan(self, options):
        timeout = options.get("timeout", 5.0)
        if timeout is None or timeout <= 0:
            raise CommandError("Timeout must be a positive number of seconds")

        result = read_rfid(timeout=timeout)
        if result.get("error"):
            return result

        if not result.get("rfid"):
            return {"error": "No RFID detected before timeout"}

        return result

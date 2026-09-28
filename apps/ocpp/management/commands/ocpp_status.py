"""Read the compact OCPP operator/display status for one charger."""

import json

from django.core.management.base import BaseCommand, CommandError

from apps.ocpp.models import Charger
from apps.ocpp.services.display_status import query_display_status


class Command(BaseCommand):
    help = "Read compact OCPP charger health for local displays and operators."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--charger", required=True, help="Charger identity.")
        parser.add_argument("--json", action="store_true", dest="json_output")

    def handle(self, *args, **options) -> None:
        try:
            payload = query_display_status(options["charger"])
        except Charger.DoesNotExist as error:
            raise CommandError(f"Unknown charger: {options['charger']}") from error

        if options["json_output"]:
            self.stdout.write(json.dumps(payload, sort_keys=True))
            return

        for key, value in payload.items():
            self.stdout.write(f"{key}: {value}")

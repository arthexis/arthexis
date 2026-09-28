"""Inspect OCPP trusted-listener and transport readiness."""

import json

from django.core.management.base import BaseCommand

from apps.ocpp.services.readiness import query_ocpp_readiness


class Command(BaseCommand):
    help = "Inspect effective OCPP trusted-listener and transport readiness."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--json", action="store_true", dest="json_output")

    def handle(self, *args, **options) -> None:
        payload = query_ocpp_readiness()

        if options["json_output"]:
            self.stdout.write(json.dumps(payload, sort_keys=True))
            return

        for key, value in payload.items():
            if isinstance(value, list):
                value = ",".join(str(item) for item in value) or "-"
            elif value is None:
                value = "-"
            self.stdout.write(f"{key}: {value}")

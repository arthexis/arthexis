from __future__ import annotations

import json

from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.ocpp.management.commands._ocpp_command_helpers import (
    add_transactions_import_arguments,
    warn_deprecated_command,
)
from apps.ocpp.transactions_io import import_transactions


def run_import_transactions(*, input_path: str) -> int:
    """Import OCPP transactions from a JSON payload file."""
    with open(input_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return import_transactions(data)


class Command(BaseCommand):
    help = "Import OCPP transactions from a JSON file"

    def add_arguments(self, parser) -> None:
        add_transactions_import_arguments(parser)

    def handle(self, *args, **options):
        warn_deprecated_command("import_transactions", "ocpp transactions import")
        call_command("ocpp", "transactions", "import", options["input"])

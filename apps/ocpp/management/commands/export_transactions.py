from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.ocpp.management.commands._ocpp_command_helpers import (
    add_transactions_export_arguments,
    warn_deprecated_command,
)
from apps.ocpp.transactions_io import export_transactions


def parse_datetime_option(value: str) -> datetime:
    """Parse CLI date/datetime values into aware datetimes."""
    parsed_datetime = parse_datetime(value)
    if parsed_datetime is None:
        parsed_date = parse_date(value)
        if parsed_date is None:
            raise CommandError(f"Invalid date/datetime: {value}")
        parsed_datetime = datetime.combine(parsed_date, datetime.min.time())
    if timezone.is_naive(parsed_datetime):
        parsed_datetime = timezone.make_aware(parsed_datetime)
    return parsed_datetime


def run_export_transactions(
    *, output_path: str, start: str | None, end: str | None, chargers: Iterable[str] | None
) -> int:
    """Export OCPP transactions to the given output JSON file."""
    start_dt = parse_datetime_option(start) if start else None
    end_dt = parse_datetime_option(end) if end else None
    data = export_transactions(start=start_dt, end=end_dt, chargers=chargers)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
    return len(data["transactions"])


class Command(BaseCommand):
    help = "Export OCPP transactions and related data to a JSON file"

    def add_arguments(self, parser) -> None:
        add_transactions_export_arguments(parser)

    def handle(self, *args, **options):
        warn_deprecated_command("export_transactions", "ocpp transactions export")
        command_options = {
            key: value
            for key, value in options.items()
            if key in {"output", "start", "end", "chargers"} and value is not None
        }
        call_command("ocpp", "transactions", "export", **command_options)

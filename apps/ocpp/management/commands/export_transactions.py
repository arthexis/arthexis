from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.ocpp.transactions_io import export_transactions


class Command(BaseCommand):
    help = "Export OCPP transactions and related data to a JSON file"

    def add_arguments(self, parser) -> None:
        parser.add_argument("output", help="Path to output JSON file")
        parser.add_argument(
            "--start",
            help="Start date (YYYY-MM-DD or ISO datetime) to filter transactions",
        )
        parser.add_argument(
            "--end",
            help="End date (YYYY-MM-DD or ISO datetime) to filter transactions",
        )
        parser.add_argument(
            "--chargers",
            nargs="*",
            help="List of charger IDs to include. If omitted all chargers are exported.",
        )

    def _parse_dt(self, value: str) -> datetime:
        dt = parse_datetime(value)
        if dt is None:
            d = parse_date(value)
            if d is None:
                raise CommandError(f"Invalid date/datetime: {value}")
            dt = datetime.combine(d, datetime.min.time())
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        return dt

    def handle(self, *args, **options):
        start = options.get("start")
        end = options.get("end")
        chargers: Iterable[str] | None = options.get("chargers")

        start_dt = self._parse_dt(start) if start else None
        end_dt = self._parse_dt(end) if end else None

        data = export_transactions(start=start_dt, end=end_dt, chargers=chargers)

        with open(options["output"], "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)

        self.stdout.write(
            self.style.SUCCESS(f"Exported {len(data['transactions'])} transactions")
        )

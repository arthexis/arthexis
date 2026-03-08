from __future__ import annotations

import random
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from apps.ocpp.models import Charger, Transaction


class Command(BaseCommand):
    """Generate deterministic OCPP charger records for public page previews."""

    DEFAULT_BASE_TIME = "2024-01-01T12:00:00+00:00"

    help = (
        "Create sample chargers and transaction history suitable for public OCPP "
        "page screenshots."
    )

    def add_arguments(self, parser):
        """Register command line arguments."""

        parser.add_argument(
            "--chargers",
            type=int,
            default=8,
            help="Number of parent charger stations to create (default: 8).",
        )
        parser.add_argument(
            "--connectors",
            type=int,
            default=4,
            help="Connector count per parent station (default: 4).",
        )
        parser.add_argument(
            "--transactions",
            type=int,
            default=15,
            help="Transaction count per connector (default: 15).",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=42,
            help="Random seed for deterministic data generation (default: 42).",
        )
        parser.add_argument(
            "--prefix",
            type=str,
            default="SAMPLE",
            help="Charger ID prefix (default: SAMPLE).",
        )
        parser.add_argument(
            "--base-time",
            type=str,
            default=self.DEFAULT_BASE_TIME,
            help=(
                "Base timestamp used to derive generated activity times "
                f"(ISO-8601, default: {self.DEFAULT_BASE_TIME})."
            ),
        )

    def _validate_options(self, options: dict[str, object]) -> tuple[int, int, int, str]:
        """Validate and normalize command options."""

        charger_count = int(options["chargers"])
        connector_count = int(options["connectors"])
        tx_per_connector = int(options["transactions"])
        prefix = str(options["prefix"]).strip().upper()

        if charger_count <= 0:
            raise CommandError("--chargers must be a positive integer")
        if connector_count <= 0:
            raise CommandError("--connectors must be a positive integer")
        if tx_per_connector <= 0:
            raise CommandError("--transactions must be a positive integer")
        if not prefix:
            raise CommandError("--prefix cannot be empty")
        return charger_count, connector_count, tx_per_connector, prefix

    def _parse_base_time(self, value: str) -> datetime:
        """Parse a deterministic base timestamp for generated records."""

        parsed = parse_datetime(str(value).strip())
        if parsed is None:
            raise CommandError("--base-time must be a valid ISO-8601 datetime")
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed

    def _create_or_get_parent(
        self,
        *,
        charger_id: str,
        station_index: int,
        now: datetime,
    ) -> tuple[Charger, int]:
        """Ensure the parent station charger exists and return creation count."""

        parent, parent_created = Charger.objects.get_or_create(
            charger_id=charger_id,
            connector_id=None,
            defaults={
                "display_name": f"Public Charger {station_index:03d}",
                "public_display": True,
                "last_status": "Available",
                "last_status_timestamp": now,
                "last_heartbeat": now,
            },
        )
        return parent, 1 if parent_created else 0

    def _populate_connector(
        self,
        *,
        parent: Charger,
        station_index: int,
        connector_index: int,
        tx_per_connector: int,
        prefix: str,
        rng: random.Random,
        now: datetime,
    ) -> tuple[int, int]:
        """Create connector plus deterministic transaction history."""

        connector, connector_created = Charger.objects.get_or_create(
            charger_id=parent.charger_id,
            connector_id=connector_index,
            defaults={
                "display_name": parent.display_name,
                "public_display": True,
                "last_status": "Available",
                "last_status_timestamp": now,
                "last_heartbeat": now,
            },
        )
        if connector.transactions.exists():
            return (1 if connector_created else 0), 0

        meter_start = rng.randint(1_000, 10_000)
        transactions_to_create: list[Transaction] = []
        for tx_index in range(tx_per_connector):
            session_start = now - timedelta(
                days=tx_per_connector - tx_index,
                minutes=rng.randint(5, 120),
            )
            session_duration_minutes = rng.randint(20, 90)
            meter_delta = rng.randint(700, 5_500)
            meter_stop = meter_start + meter_delta
            transactions_to_create.append(
                Transaction(
                    charger=connector,
                    connector_id=connector_index,
                    start_time=session_start,
                    stop_time=session_start + timedelta(minutes=session_duration_minutes),
                    meter_start=meter_start,
                    meter_stop=meter_stop,
                    rfid=f"RFID-{station_index:03d}-{connector_index:02d}",
                    ocpp_transaction_id=(
                        f"{prefix}-{station_index:03d}-{connector_index:02d}-{tx_index:04d}"
                    ),
                )
            )
            meter_start = meter_stop + rng.randint(50, 400)

        active_start = now - timedelta(minutes=rng.randint(8, 50))
        active_meter_start = meter_start + rng.randint(100, 800)
        transactions_to_create.append(
            Transaction(
                charger=connector,
                connector_id=connector_index,
                start_time=active_start,
                meter_start=active_meter_start,
                rfid=f"LIVE-{station_index:03d}-{connector_index:02d}",
                ocpp_transaction_id=f"LIVE-{prefix}-{station_index:03d}-{connector_index:02d}",
            )
        )
        Transaction.objects.bulk_create(transactions_to_create)

        connector.last_status = "Charging"
        connector.last_status_timestamp = now
        connector.last_heartbeat = now
        connector.save(update_fields=["last_status", "last_status_timestamp", "last_heartbeat"])
        return (1 if connector_created else 0), len(transactions_to_create)

    @transaction.atomic
    def handle(self, *args, **options):
        """Create parent/connector chargers plus historical and active sessions."""

        charger_count, connector_count, tx_per_connector, prefix = self._validate_options(
            options
        )

        rng = random.Random(options["seed"])
        now = self._parse_base_time(str(options["base_time"]))

        created_chargers = 0
        created_transactions = 0

        for station_index in range(1, charger_count + 1):
            charger_id = f"{prefix}-{station_index:03d}"
            parent, parent_created = self._create_or_get_parent(
                charger_id=charger_id,
                station_index=station_index,
                now=now,
            )
            created_chargers += parent_created

            for connector_index in range(1, connector_count + 1):
                chargers_created, tx_created = self._populate_connector(
                    parent=parent,
                    station_index=station_index,
                    connector_index=connector_index,
                    tx_per_connector=tx_per_connector,
                    prefix=prefix,
                    rng=rng,
                    now=now,
                )
                created_chargers += chargers_created
                created_transactions += tx_created

        self.stdout.write(
            self.style.SUCCESS(
                "Sample data ready: "
                f"{created_chargers} chargers created, "
                f"{created_transactions} transactions created."
            )
        )

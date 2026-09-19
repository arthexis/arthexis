from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.ocpp.models import Charger, OcppTransaction, StationModel


class ChargerCommandTests(TestCase):
    def setUp(self) -> None:
        station_model = StationModel.objects.create(
            vendor="ACME",
            model="Model",
            preferred_protocol="ocpp1.6",
        )
        self.charger = Charger.objects.create(
            identity="charger-1",
            station_model=station_model,
        )
        OcppTransaction.objects.create(
            charger=self.charger,
            remote_id="transaction-1",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            stopped_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
            energy_kwh=Decimal("1.2500"),
        )

    def test_command_renders_the_app_wide_fleet_snapshot(self) -> None:
        output = StringIO()

        call_command("charger", stdout=output)

        rendered = output.getvalue()
        self.assertIn("Charger snapshot:", rendered)
        self.assertIn("charger-1", rendered)
        self.assertIn("ocpp1.6", rendered)
        self.assertIn("1.2500 kWh", rendered)

    def test_report_selection_rejects_unknown_duplicate_and_mixed_targets(self) -> None:
        with self.assertRaisesMessage(CommandError, "Unknown charger"):
            call_command("charger", "--charger", "missing")
        with self.assertRaisesMessage(CommandError, "only once"):
            call_command(
                "charger",
                "--charger",
                self.charger.identity,
                "--charger",
                self.charger.identity,
            )
        with self.assertRaisesMessage(CommandError, "--all by itself"):
            call_command("charger", "--all", "--charger", self.charger.identity)

    def test_command_does_not_accept_model_mutation_verbs(self) -> None:
        with self.assertRaisesMessage(CommandError, "unrecognized arguments: reset"):
            call_command("charger", "reset", "--charger", self.charger.identity)

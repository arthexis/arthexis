from datetime import UTC, datetime
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.ocpp.models import Charger, ChargerConnection, OcppTransaction, StationModel


class FleetCommandTests(TestCase):
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
        ChargerConnection.objects.create(
            charger=self.charger,
            channel_name="fleet-channel",
            protocol="ocpp1.6",
        )
        OcppTransaction.objects.create(
            charger=self.charger,
            remote_id="transaction-last",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            stopped_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
        )
        OcppTransaction.objects.create(
            charger=self.charger,
            remote_id="transaction-active",
            started_at=datetime(2026, 1, 1, 2, tzinfo=UTC),
        )

    def test_command_renders_the_app_wide_fleet_snapshot(self) -> None:
        output = StringIO()

        call_command("fleet", stdout=output)

        rendered = output.getvalue()
        self.assertIn("Fleet snapshot:", rendered)
        self.assertIn("Charger", rendered)
        self.assertIn("Enabled", rendered)
        self.assertIn("Connection", rendered)
        self.assertIn("State", rendered)
        self.assertIn("Protocol", rendered)
        self.assertIn("Active TX", rendered)
        self.assertIn("Last TX", rendered)
        self.assertIn("Last contact", rendered)
        self.assertIn("charger-1", rendered)
        self.assertIn("yes", rendered)
        self.assertIn("connected", rendered)
        self.assertIn("charging", rendered)
        self.assertIn("ocpp1.6", rendered)
        self.assertIn("transaction-active", rendered)
        self.assertIn("transaction-last", rendered)

    def test_report_selection_rejects_unknown_duplicate_and_mixed_targets(self) -> None:
        with self.assertRaisesMessage(CommandError, "Unknown charger"):
            call_command("fleet", "--charger", "missing")
        with self.assertRaisesMessage(CommandError, "only once"):
            call_command(
                "fleet",
                "--charger",
                self.charger.identity,
                "--charger",
                self.charger.identity,
            )
        with self.assertRaisesMessage(CommandError, "--all by itself"):
            call_command("fleet", "--all", "--charger", self.charger.identity)

    def test_command_does_not_accept_model_mutation_verbs(self) -> None:
        with self.assertRaisesMessage(CommandError, "unrecognized arguments: reset"):
            call_command("fleet", "reset", "--charger", self.charger.identity)


    def test_command_is_one_shot_and_handles_an_empty_fleet(self) -> None:
        Charger.objects.all().delete()
        output = StringIO()

        call_command("fleet", stdout=output)

        rendered = output.getvalue()
        self.assertEqual(rendered.count("Fleet snapshot:"), 1)
        self.assertIn("No chargers found.", rendered)

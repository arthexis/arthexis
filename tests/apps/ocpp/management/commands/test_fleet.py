from datetime import datetime, timezone
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.ocpp.models import Charger
from tests.apps.ocpp.builders import charger, connection, station_model, transaction


class FleetCommandTests(TestCase):
    def setUp(self) -> None:
        model = station_model(protocol="ocpp1.6")
        self.charger = charger("charger-1", station=model)
        connection(self.charger, channel_name="fleet-channel")
        transaction(
            self.charger,
            "transaction-last",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            stopped_at=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        )
        transaction(
            self.charger,
            "transaction-active",
            started_at=datetime(2026, 1, 1, 2, tzinfo=timezone.utc),
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

    def test_opposing_filters_are_mutually_exclusive(self) -> None:
        with self.assertRaisesMessage(CommandError, "not allowed with argument"):
            call_command("fleet", "--enabled", "--disabled")
        with self.assertRaisesMessage(CommandError, "not allowed with argument"):
            call_command("fleet", "--connected", "--disconnected")
        with self.assertRaisesMessage(CommandError, "not allowed with argument"):
            call_command("fleet", "--charging", "--idle")
        with self.assertRaisesMessage(CommandError, "not allowed with argument"):
            call_command("fleet", "--charging", "--unresolved")

    def test_detail_flag_requests_the_richer_view(self) -> None:
        output = StringIO()

        call_command("fleet", "--detail", stdout=output)

        rendered = output.getvalue()
        self.assertIn("Connectors", rendered)
        self.assertIn("Active since", rendered)
        self.assertIn("Last stopped", rendered)
        self.assertIn("Energy total", rendered)
        self.assertIn("Recovery unresolved", rendered)
        self.assertIn("Energy unresolved", rendered)


    def test_unresolved_filter_and_state_are_visible_to_operators(self) -> None:
        uncertain = Charger.objects.get(pk=self.charger.pk).transactions.get(
            remote_id="transaction-active"
        )
        uncertain.recovery_state = uncertain.RecoveryState.UNRESOLVED
        uncertain.save(update_fields=("recovery_state",))
        output = StringIO()

        call_command("fleet", "--unresolved", stdout=output)

        rendered = output.getvalue()
        self.assertIn("charger-1", rendered)
        self.assertIn("unresolved", rendered)
        self.assertNotIn("charging", rendered)

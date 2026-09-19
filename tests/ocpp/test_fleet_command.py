from datetime import UTC, datetime
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.ocpp.management.charger.selection import select_chargers
from apps.ocpp.models import Charger
from tests.ocpp.builders import charger, connection, station_model, transaction


class FleetCommandTests(TestCase):
    def setUp(self) -> None:
        model = station_model(protocol="ocpp1.6")
        self.charger = charger("charger-1", station=model)
        connection(self.charger, channel_name="fleet-channel")
        transaction(
            self.charger,
            "transaction-last",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            stopped_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
        )
        transaction(
            self.charger,
            "transaction-active",
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

    def test_filters_compose_across_dimensions(self) -> None:
        idle = charger("charger-idle")
        connection(idle, channel_name="idle-channel")
        disabled = charger("charger-disabled", active=False)

        output = StringIO()
        call_command("fleet", "--enabled", "--connected", stdout=output)
        rendered = output.getvalue()

        self.assertIn(self.charger.identity, rendered)
        self.assertIn(idle.identity, rendered)
        self.assertNotIn(disabled.identity, rendered)

        output = StringIO()
        call_command("fleet", "--idle", stdout=output)
        rendered = output.getvalue()

        self.assertIn(idle.identity, rendered)
        self.assertNotIn(self.charger.identity, rendered)

    def test_opposing_filters_are_mutually_exclusive(self) -> None:
        with self.assertRaisesMessage(CommandError, "not allowed with argument"):
            call_command("fleet", "--enabled", "--disabled")
        with self.assertRaisesMessage(CommandError, "not allowed with argument"):
            call_command("fleet", "--connected", "--disconnected")
        with self.assertRaisesMessage(CommandError, "not allowed with argument"):
            call_command("fleet", "--charging", "--idle")

    def test_detail_mode_adds_richer_columns_without_changing_default_view(self) -> None:
        default_output = StringIO()
        call_command("fleet", stdout=default_output)
        default = default_output.getvalue()

        self.assertNotIn("Connectors", default)
        self.assertNotIn("Active since", default)
        self.assertNotIn("Last stopped", default)
        self.assertNotIn("Energy total", default)
        self.assertNotIn("Unresolved", default)

        detail_output = StringIO()
        call_command("fleet", "--detail", stdout=detail_output)
        detail = detail_output.getvalue()

        self.assertIn("Connectors", detail)
        self.assertIn("Active since", detail)
        self.assertIn("Last stopped", detail)
        self.assertIn("Energy total", detail)
        self.assertIn("Unresolved", detail)
        self.assertIn("transaction-active", detail)
        self.assertIn("transaction-last", detail)


    def test_selection_rejects_unknown_filter_names(self) -> None:
        with self.assertRaisesMessage(CommandError, "Unknown fleet filter"):
            list(
                select_chargers(
                    identities=[],
                    select_all=False,
                    filters=("reset",),
                )
            )

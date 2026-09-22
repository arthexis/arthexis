from datetime import datetime, timezone
from decimal import Decimal

from django.test import SimpleTestCase

from apps.ocpp.domain.snapshots import ChargerSnapshot
from apps.ocpp.management.charger.render import render_snapshots


class RecordingStream:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, value: str) -> None:
        self.lines.append(value)


class RecordingCommand:
    def __init__(self) -> None:
        self.stdout = RecordingStream()


class ChargerRenderTests(SimpleTestCase):
    def _snapshot(self) -> ChargerSnapshot:
        return ChargerSnapshot(
            identity="charger-1",
            enabled=True,
            state="charging",
            configured_protocol="ocpp1.6",
            connection_state="connected",
            connector_states=("1:Charging",),
            active_transactions=1,
            current_transaction_id="transaction-active",
            current_transaction_started=datetime(2026, 1, 1, 2, tzinfo=timezone.utc),
            last_transaction_id="transaction-last",
            last_transaction_stopped=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
            energy_kwh=Decimal("1.2500"),
            unresolved_sessions=1,
            unresolved_energy_sessions=2,
            last_contact=datetime(2026, 1, 1, 2, 5, tzinfo=timezone.utc),
        )

    def test_default_view_omits_detail_columns(self) -> None:
        command = RecordingCommand()

        render_snapshots(command, [self._snapshot()])

        rendered = "\n".join(command.stdout.lines)
        self.assertNotIn("Connectors", rendered)
        self.assertNotIn("Active since", rendered)
        self.assertNotIn("Last stopped", rendered)
        self.assertNotIn("Energy total", rendered)
        self.assertNotIn("Recovery unresolved", rendered)
        self.assertNotIn("Energy unresolved", rendered)

    def test_detail_view_adds_richer_columns_and_values(self) -> None:
        command = RecordingCommand()

        render_snapshots(command, [self._snapshot()], detail=True)

        rendered = "\n".join(command.stdout.lines)
        self.assertIn("Connectors", rendered)
        self.assertIn("Active since", rendered)
        self.assertIn("Last stopped", rendered)
        self.assertIn("Energy total", rendered)
        self.assertIn("Recovery unresolved", rendered)
        self.assertIn("Energy unresolved", rendered)
        self.assertIn("transaction-active", rendered)
        self.assertIn("transaction-last", rendered)
        self.assertIn("1.2500 kWh", rendered)

    def test_empty_fleet_has_an_explicit_message(self) -> None:
        command = RecordingCommand()

        render_snapshots(command, [])

        self.assertIn("No chargers found.", command.stdout.lines)

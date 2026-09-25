from datetime import datetime, timezone
from decimal import Decimal

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


def snapshot() -> ChargerSnapshot:
    return ChargerSnapshot(
        identity="charger-1",
        enabled=True,
        state="charging",
        configured_protocol="ocpp1.6",
        connection_state="connected",
        connection_last_seen_at=datetime(2026, 1, 1, 2, 4, tzinfo=timezone.utc),
        connection_lease_expires_at=datetime(2026, 1, 1, 2, 6, tzinfo=timezone.utc),
        connector_states=("1:Charging",),
        state_reason="active session transaction-active",
        waiting_for="transaction end or newer charger evidence",
        active_transactions=1,
        current_transaction_id="transaction-active",
        current_transaction_started=datetime(2026, 1, 1, 2, tzinfo=timezone.utc),
        current_transaction_last_activity=datetime(
            2026, 1, 1, 2, 3, tzinfo=timezone.utc
        ),
        last_transaction_id="transaction-last",
        last_transaction_stopped=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        energy_kwh=Decimal("1.2500"),
        unresolved_sessions=1,
        latest_unresolved_transaction_id="transaction-unresolved",
        latest_unresolved_activity=datetime(
            2026, 1, 1, 1, 30, tzinfo=timezone.utc
        ),
        cleared_sessions=1,
        last_cleared_transaction_id="transaction-cleared",
        last_recovery_cleared_at=datetime(2026, 1, 1, 1, 45, tzinfo=timezone.utc),
        last_recovery_clear_reason="verified idle",
        recovery_required_operations=1,
        recovery_operation_summaries=(
            "RemoteStopTransaction [reconcile] attempts=1",
        ),
        unresolved_energy_sessions=2,
        authority_cutover_at=datetime(2026, 1, 1, 0, tzinfo=timezone.utc),
        historical_sessions=3,
        historical_open_sessions=1,
        historical_oldest_started=datetime(2023, 1, 1, 0, tzinfo=timezone.utc),
        historical_latest_activity=datetime(2025, 12, 31, 23, 30, tzinfo=timezone.utc),
        last_contact=datetime(2026, 1, 1, 2, 5, tzinfo=timezone.utc),
    )


def test_default_view_omits_detail_columns() -> None:
    command = RecordingCommand()

    render_snapshots(command, [snapshot()])

    rendered = "\n".join(command.stdout.lines)
    for column in (
        "Connectors",
        "Active since",
        "Last stopped",
        "Energy total",
        "Recovery unresolved",
        "Energy unresolved",
        "Authority cutover",
        "Historical TX",
        "Historical open",
    ):
        assert column not in rendered


def test_detail_view_adds_richer_columns_and_values() -> None:
    command = RecordingCommand()

    render_snapshots(command, [snapshot()], detail=True)

    rendered = "\n".join(command.stdout.lines)
    for value in (
        "Connectors",
        "Active since",
        "Last stopped",
        "Energy total",
        "Recovery unresolved",
        "Energy unresolved",
        "Authority cutover",
        "Historical TX",
        "Historical open",
        "Historical oldest",
        "Historical latest",
        "transaction-active",
        "transaction-last",
        "1.2500 kWh",
    ):
        assert value in rendered


def test_empty_fleet_has_an_explicit_message() -> None:
    command = RecordingCommand()

    render_snapshots(command, [])

    assert "No chargers found." in command.stdout.lines

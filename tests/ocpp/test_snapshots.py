from datetime import datetime, timezone
from decimal import Decimal

from django.test import TestCase

from apps.ocpp.domain.snapshots import snapshot_charger, snapshot_chargers
from tests.ocpp.builders import (
    charger,
    connection,
    connector,
    station_model,
    transaction,
)


class ChargerSnapshotTests(TestCase):
    def test_snapshot_marks_unresolved_energy_without_inventing_a_total(self) -> None:
        model = station_model(protocol="ocpp2.0.1")
        selected = charger("charger-1", station=model)
        connector(selected, number=1, status="Charging")
        connection(
            selected,
            channel_name="specific.channel",
            protocol="ocpp2.0.1",
        )
        transaction(
            selected,
            "complete",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            stopped_at=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
            energy_kwh=Decimal("1.2500"),
        )
        transaction(
            selected,
            "unresolved",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            stopped_at=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        )
        transaction(
            selected,
            "active",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

        snapshot = snapshot_charger(selected)

        self.assertTrue(snapshot.enabled)
        self.assertEqual(snapshot.state, "charging")
        self.assertEqual(snapshot.configured_protocol, "ocpp2.0.1")
        self.assertEqual(snapshot.connection_state, "connected")
        self.assertEqual(snapshot.connector_states, ("1:Charging",))
        self.assertEqual(snapshot.active_transactions, 1)
        self.assertEqual(snapshot.current_transaction_id, "active")
        self.assertEqual(
            snapshot.current_transaction_started,
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(snapshot.last_transaction_id, "unresolved")
        self.assertEqual(
            snapshot.last_transaction_stopped,
            datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(snapshot.energy_kwh, Decimal("1.25"))
        self.assertEqual(snapshot.unresolved_sessions, 1)
        self.assertEqual(snapshot_chargers(), [snapshot])

    def test_snapshot_reports_unknown_energy_as_none(self) -> None:
        selected = charger("charger-1")

        snapshot = snapshot_charger(selected)

        self.assertIsNone(snapshot.energy_kwh)
        self.assertEqual(snapshot.unresolved_sessions, 0)
        self.assertEqual(snapshot.connection_state, "disconnected")
        self.assertTrue(snapshot.enabled)
        self.assertEqual(snapshot.state, "offline")
        self.assertIsNone(snapshot.current_transaction_id)
        self.assertIsNone(snapshot.current_transaction_started)
        self.assertIsNone(snapshot.last_transaction_id)
        self.assertIsNone(snapshot.last_transaction_stopped)

    def test_snapshot_state_precedence_distinguishes_disabled_idle_and_charging(
        self,
    ) -> None:
        disabled = charger("disabled", active=False)
        connection(disabled, channel_name="disabled-channel")
        transaction(
            disabled,
            "disabled-active",
            started_at=datetime(2026, 9, 19, 12, tzinfo=timezone.utc),
        )

        idle = charger("idle")
        connection(idle, channel_name="idle-channel")

        charging = charger("charging")
        connection(charging, channel_name="charging-channel")
        transaction(
            charging,
            "charging-active",
            started_at=datetime(2026, 9, 19, 13, tzinfo=timezone.utc),
        )

        self.assertEqual(snapshot_charger(disabled).state, "disabled")
        self.assertEqual(snapshot_charger(idle).state, "idle")
        self.assertEqual(snapshot_charger(charging).state, "charging")

    def test_snapshot_chargers_prefetches_shared_read_data(self) -> None:
        for index in range(3):
            selected = charger(f"charger-{index}")
            connection(selected, channel_name=f"channel-{index}")
            connector(selected)
            transaction(
                selected,
                f"tx-{index}",
                started_at=datetime(2026, 9, 19, 12 + index, tzinfo=timezone.utc),
            )

        with self.assertNumQueries(3):
            snapshots = snapshot_chargers()

        self.assertEqual(len(snapshots), 3)
        self.assertTrue(all(snapshot.state == "charging" for snapshot in snapshots))

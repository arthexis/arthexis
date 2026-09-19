from datetime import UTC, datetime
from decimal import Decimal

from django.test import TestCase

from apps.ocpp.domain.snapshots import snapshot_charger, snapshot_chargers
from apps.ocpp.models import (
    Charger,
    ChargerConnection,
    Connector,
    OcppTransaction,
    StationModel,
)


class ChargerSnapshotTests(TestCase):
    def test_snapshot_marks_unresolved_energy_without_inventing_a_total(self) -> None:
        station_model = StationModel.objects.create(
            vendor="ACME",
            model="Model",
            preferred_protocol="ocpp2.0.1",
        )
        charger = Charger.objects.create(
            identity="charger-1",
            station_model=station_model,
        )
        Connector.objects.create(charger=charger, number=1, status="Charging")
        ChargerConnection.objects.create(
            charger=charger,
            channel_name="specific.channel",
            protocol="ocpp2.0.1",
        )
        OcppTransaction.objects.create(
            charger=charger,
            remote_id="complete",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            stopped_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
            energy_kwh=Decimal("1.2500"),
        )
        OcppTransaction.objects.create(
            charger=charger,
            remote_id="unresolved",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            stopped_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
        )
        OcppTransaction.objects.create(
            charger=charger,
            remote_id="active",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

        snapshot = snapshot_charger(charger)

        self.assertTrue(snapshot.enabled)
        self.assertEqual(snapshot.state, "charging")
        self.assertEqual(snapshot.configured_protocol, "ocpp2.0.1")
        self.assertEqual(snapshot.connection_state, "connected")
        self.assertEqual(snapshot.connector_states, ("1:Charging",))
        self.assertEqual(snapshot.active_transactions, 1)
        self.assertEqual(snapshot.current_transaction_id, "active")
        self.assertEqual(
            snapshot.current_transaction_started,
            datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.assertEqual(snapshot.last_transaction_id, "unresolved")
        self.assertEqual(
            snapshot.last_transaction_stopped,
            datetime(2026, 1, 1, 1, tzinfo=UTC),
        )
        self.assertEqual(snapshot.energy_kwh, Decimal("1.25"))
        self.assertEqual(snapshot.unresolved_sessions, 1)
        self.assertEqual(snapshot_chargers(), [snapshot])

    def test_snapshot_reports_unknown_energy_as_none(self) -> None:
        charger = Charger.objects.create(identity="charger-1")

        snapshot = snapshot_charger(charger)

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
        disabled = Charger.objects.create(identity="disabled", active=False)
        ChargerConnection.objects.create(
            charger=disabled,
            channel_name="disabled-channel",
            protocol="ocpp1.6",
        )
        OcppTransaction.objects.create(
            charger=disabled,
            remote_id="disabled-active",
            started_at=datetime(2026, 9, 19, 12, tzinfo=UTC),
        )

        idle = Charger.objects.create(identity="idle")
        ChargerConnection.objects.create(
            charger=idle,
            channel_name="idle-channel",
            protocol="ocpp1.6",
        )

        charging = Charger.objects.create(identity="charging")
        ChargerConnection.objects.create(
            charger=charging,
            channel_name="charging-channel",
            protocol="ocpp1.6",
        )
        OcppTransaction.objects.create(
            charger=charging,
            remote_id="charging-active",
            started_at=datetime(2026, 9, 19, 13, tzinfo=UTC),
        )

        self.assertEqual(snapshot_charger(disabled).state, "disabled")
        self.assertEqual(snapshot_charger(idle).state, "idle")
        self.assertEqual(snapshot_charger(charging).state, "charging")

    def test_snapshot_chargers_prefetches_shared_read_data(self) -> None:
        for index in range(3):
            charger = Charger.objects.create(identity=f"charger-{index}")
            ChargerConnection.objects.create(
                charger=charger,
                channel_name=f"channel-{index}",
                protocol="ocpp1.6",
            )
            Connector.objects.create(
                charger=charger,
                number=1,
                status="Available",
            )
            OcppTransaction.objects.create(
                charger=charger,
                remote_id=f"tx-{index}",
                started_at=datetime(2026, 9, 19, 12 + index, tzinfo=UTC),
            )

        with self.assertNumQueries(3):
            snapshots = snapshot_chargers()

        self.assertEqual(len(snapshots), 3)
        self.assertTrue(all(snapshot.state == "charging" for snapshot in snapshots))

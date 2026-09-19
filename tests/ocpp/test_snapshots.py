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

        self.assertEqual(snapshot.configured_protocol, "ocpp2.0.1")
        self.assertEqual(snapshot.connection_state, "connected")
        self.assertEqual(snapshot.connector_states, ("1:Charging",))
        self.assertEqual(snapshot.active_transactions, 1)
        self.assertEqual(snapshot.energy_kwh, Decimal("1.25"))
        self.assertEqual(snapshot.unresolved_sessions, 1)
        self.assertEqual(snapshot_chargers(), [snapshot])

    def test_snapshot_reports_unknown_energy_as_none(self) -> None:
        charger = Charger.objects.create(identity="charger-1")

        snapshot = snapshot_charger(charger)

        self.assertIsNone(snapshot.energy_kwh)
        self.assertEqual(snapshot.unresolved_sessions, 0)
        self.assertEqual(snapshot.connection_state, "disconnected")

from datetime import datetime, timezone

from django.test import TestCase

from apps.ocpp.domain.sessions import (
    record_meter_values,
    record_v201_transaction_event,
    start_transaction,
    stop_transaction,
)
from apps.ocpp.models import OcppTransaction
from tests.apps.ocpp.builders import charger


class TransactionRecoveryLifecycleTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("recovery-domain")

    def test_v16_lifecycle_tracks_activity_and_completion_state(self) -> None:
        started_at = datetime(2026, 9, 22, 10, tzinfo=timezone.utc)
        meter_at = datetime(2026, 9, 22, 10, 15, tzinfo=timezone.utc)
        stopped_at = datetime(2026, 9, 22, 10, 30, tzinfo=timezone.utc)

        selected = start_transaction(
            charger=self.charger,
            connector_id=1,
            id_tag="card",
            account=None,
            meter_start=100,
            timestamp=started_at.isoformat(),
        )
        self.assertEqual(
            selected.recovery_state,
            OcppTransaction.RecoveryState.ACTIVE,
        )
        self.assertEqual(selected.last_activity_at, started_at)

        record_meter_values(
            transaction_id=selected.pk,
            charger=self.charger,
            meter_values=[
                {
                    "timestamp": meter_at.isoformat(),
                    "sampledValue": [{"value": "125"}],
                }
            ],
        )
        selected.refresh_from_db()
        self.assertEqual(selected.last_activity_at, meter_at)

        stop_transaction(
            transaction_id=selected.pk,
            charger=self.charger,
            meter_stop=150,
            timestamp=stopped_at.isoformat(),
        )
        selected.refresh_from_db()
        self.assertEqual(
            selected.recovery_state,
            OcppTransaction.RecoveryState.COMPLETED,
        )
        self.assertEqual(selected.last_activity_at, stopped_at)
        self.assertEqual(selected.stopped_at, stopped_at)

    def test_older_meter_sample_does_not_move_activity_backwards(self) -> None:
        started_at = datetime(2026, 9, 22, 10, tzinfo=timezone.utc)
        selected = start_transaction(
            charger=self.charger,
            connector_id=1,
            id_tag="card",
            account=None,
            meter_start=100,
            timestamp=started_at.isoformat(),
        )

        record_meter_values(
            transaction_id=selected.pk,
            charger=self.charger,
            meter_values=[
                {
                    "timestamp": datetime(
                        2026, 9, 22, 9, 55, tzinfo=timezone.utc
                    ).isoformat(),
                    "sampledValue": [{"value": "95"}],
                }
            ],
        )
        selected.refresh_from_db()

        self.assertEqual(selected.last_activity_at, started_at)

    def test_v201_ended_event_marks_transaction_completed(self) -> None:
        started_at = datetime(2026, 9, 22, 10, tzinfo=timezone.utc)
        ended_at = datetime(2026, 9, 22, 11, tzinfo=timezone.utc)

        selected = record_v201_transaction_event(
            charger=self.charger,
            event_type="Started",
            transaction_id="remote-1",
            id_token="card",
            evse_id=1,
            connector_id=1,
            timestamp=started_at.isoformat(),
        )
        self.assertEqual(
            selected.recovery_state,
            OcppTransaction.RecoveryState.ACTIVE,
        )

        record_v201_transaction_event(
            charger=self.charger,
            event_type="Ended",
            transaction_id="remote-1",
            id_token="",
            evse_id=1,
            connector_id=1,
            timestamp=ended_at.isoformat(),
        )
        selected.refresh_from_db()

        self.assertEqual(
            selected.recovery_state,
            OcppTransaction.RecoveryState.COMPLETED,
        )
        self.assertEqual(selected.last_activity_at, ended_at)
        self.assertEqual(selected.stopped_at, ended_at)

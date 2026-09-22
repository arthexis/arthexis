from datetime import datetime, timezone

from django.test import TestCase

from apps.ocpp.domain.sessions import (
    reconcile_connector_status,
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


    def test_available_status_marks_matching_active_transaction_unresolved(self) -> None:
        started_at = datetime(2026, 9, 22, 10, tzinfo=timezone.utc)
        observed_at = datetime(2026, 9, 22, 10, 20, tzinfo=timezone.utc)
        selected = start_transaction(
            charger=self.charger,
            connector_id=1,
            id_tag="card",
            account=None,
            meter_start=100,
            timestamp=started_at.isoformat(),
        )

        reconcile_connector_status(
            charger=self.charger,
            connector_number=1,
            status="Available",
            observed_at=observed_at.isoformat(),
        )
        selected.refresh_from_db()

        self.assertEqual(
            selected.recovery_state,
            OcppTransaction.RecoveryState.UNRESOLVED,
        )
        self.assertIsNone(selected.stopped_at)
        self.assertEqual(selected.last_activity_at, observed_at)

    def test_non_available_status_does_not_force_transaction_unresolved(self) -> None:
        selected = start_transaction(
            charger=self.charger,
            connector_id=1,
            id_tag="card",
            account=None,
            meter_start=100,
            timestamp="2026-09-22T10:00:00Z",
        )

        reconcile_connector_status(
            charger=self.charger,
            connector_number=1,
            status="Faulted",
            observed_at="2026-09-22T10:20:00Z",
        )
        selected.refresh_from_db()

        self.assertEqual(
            selected.recovery_state,
            OcppTransaction.RecoveryState.ACTIVE,
        )
        self.assertIsNone(selected.stopped_at)

    def test_buffered_meter_before_available_evidence_does_not_reactivate(self) -> None:
        selected = start_transaction(
            charger=self.charger,
            connector_id=1,
            id_tag="card",
            account=None,
            meter_start=100,
            timestamp="2026-09-22T10:00:00Z",
        )
        reconcile_connector_status(
            charger=self.charger,
            connector_number=1,
            status="Available",
            observed_at="2026-09-22T10:20:00Z",
        )

        record_meter_values(
            transaction_id=selected.pk,
            charger=self.charger,
            meter_values=[
                {
                    "timestamp": "2026-09-22T10:10:00Z",
                    "sampledValue": [{"value": "120"}],
                }
            ],
        )
        selected.refresh_from_db()

        self.assertEqual(
            selected.recovery_state,
            OcppTransaction.RecoveryState.UNRESOLVED,
        )
        self.assertEqual(
            selected.last_activity_at,
            datetime(2026, 9, 22, 10, 20, tzinfo=timezone.utc),
        )

    def test_newer_meter_evidence_reactivates_unresolved_transaction(self) -> None:
        selected = start_transaction(
            charger=self.charger,
            connector_id=1,
            id_tag="card",
            account=None,
            meter_start=100,
            timestamp="2026-09-22T10:00:00Z",
        )
        reconcile_connector_status(
            charger=self.charger,
            connector_number=1,
            status="Available",
            observed_at="2026-09-22T10:20:00Z",
        )

        record_meter_values(
            transaction_id=selected.pk,
            charger=self.charger,
            meter_values=[
                {
                    "timestamp": "2026-09-22T10:25:00Z",
                    "sampledValue": [{"value": "125"}],
                }
            ],
        )
        selected.refresh_from_db()

        self.assertEqual(
            selected.recovery_state,
            OcppTransaction.RecoveryState.ACTIVE,
        )
        self.assertEqual(
            selected.last_activity_at,
            datetime(2026, 9, 22, 10, 25, tzinfo=timezone.utc),
        )

    def test_offline_transaction_event_remains_unresolved_until_newer_live_event(
        self,
    ) -> None:
        selected = record_v201_transaction_event(
            charger=self.charger,
            event_type="Started",
            transaction_id="remote-offline",
            id_token="card",
            evse_id=1,
            connector_id=1,
            timestamp="2026-09-22T10:00:00Z",
            live_evidence=False,
        )

        self.assertEqual(
            selected.recovery_state,
            OcppTransaction.RecoveryState.UNRESOLVED,
        )

        record_v201_transaction_event(
            charger=self.charger,
            event_type="Updated",
            transaction_id="remote-offline",
            id_token="",
            evse_id=1,
            connector_id=1,
            timestamp="2026-09-22T10:05:00Z",
            live_evidence=True,
        )
        selected.refresh_from_db()

        self.assertEqual(
            selected.recovery_state,
            OcppTransaction.RecoveryState.ACTIVE,
        )

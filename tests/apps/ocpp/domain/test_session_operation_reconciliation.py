from datetime import datetime, timedelta, timezone

from django.test import TestCase

from apps.ocpp.domain.operations import (
    create_operation,
    reconcile_session_operation,
    reconcile_session_operations,
)
from apps.ocpp.models import ProtocolOperation
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from apps.ocpp.tasks import reconcile_ambiguous_session_operations
from tests.apps.ocpp.builders import charger, connector, transaction


class SessionOperationReconciliationTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("reconcile-session")
        self.attempt_at = datetime(2026, 9, 23, 10, tzinfo=timezone.utc)

    def _ambiguous(
        self,
        *,
        version: ProtocolVersion,
        action: str,
        payload: dict[str, object],
    ) -> ProtocolOperation:
        operation = create_operation(
            charger=self.charger,
            version=version,
            direction=Direction.CSMS_TO_CHARGE_POINT,
            action=action,
            request_payload=payload,
        )
        operation.status = ProtocolOperation.Status.RECOVERY_REQUIRED
        operation.attempt_count = 1
        operation.first_attempt_at = self.attempt_at
        operation.last_attempt_at = self.attempt_at
        operation.save(
            update_fields=(
                "status",
                "attempt_count",
                "first_attempt_at",
                "last_attempt_at",
            )
        )
        return operation

    def test_v16_remote_start_settles_from_matching_later_transaction(self) -> None:
        selected_connector = connector(self.charger, number=1)
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="RemoteStartTransaction",
            payload={"idTag": "member-1", "connectorId": 1},
        )
        selected = transaction(
            self.charger,
            "started-after-ambiguity",
            started_at=self.attempt_at + timedelta(seconds=5),
            connector=selected_connector,
            id_tag="member-1",
        )

        result = reconcile_session_operation(operation)

        self.assertEqual(result.status, ProtocolOperation.Status.COMPLETED)
        self.assertIsNone(result.response_payload)
        self.assertIsNotNone(result.reconciled_at)
        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.ACHIEVED,
        )
        self.assertIn(selected.remote_id, result.reconciliation_basis)
        self.assertIsNotNone(result.reconciliation_checked_at)

    def test_v201_request_start_settles_from_matching_evse_transaction(self) -> None:
        selected_connector = connector(self.charger, number=3001)
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_201,
            action="RequestStartTransaction",
            payload={
                "idToken": {"idToken": "member-2"},
                "remoteStartId": 42,
                "evseId": 3,
            },
        )
        selected = transaction(
            self.charger,
            "remote-201",
            started_at=self.attempt_at + timedelta(seconds=10),
            connector=selected_connector,
            id_tag="member-2",
        )

        result = reconcile_session_operation(operation)

        self.assertEqual(result.status, ProtocolOperation.Status.COMPLETED)
        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.ACHIEVED,
        )
        self.assertIn(selected.remote_id, result.reconciliation_basis)

    def test_remote_start_without_positive_session_evidence_stays_ambiguous(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="RemoteStartTransaction",
            payload={"idTag": "member-1"},
        )

        result = reconcile_session_operation(operation)

        self.assertEqual(
            result.status,
            ProtocolOperation.Status.RECOVERY_REQUIRED,
        )
        self.assertIsNotNone(result.reconciliation_checked_at)
        self.assertIsNone(result.reconciled_at)
        self.assertEqual(result.reconciliation_resolution, "")
        self.assertEqual(result.reconciliation_basis, "")

    def test_transaction_before_remote_start_attempt_does_not_prove_achievement(self) -> None:
        transaction(
            self.charger,
            "older-session",
            started_at=self.attempt_at - timedelta(seconds=1),
            id_tag="member-1",
        )
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="RemoteStartTransaction",
            payload={"idTag": "member-1"},
        )

        result = reconcile_session_operation(operation)

        self.assertEqual(
            result.status,
            ProtocolOperation.Status.RECOVERY_REQUIRED,
        )

    def test_v16_remote_stop_settles_when_server_transaction_is_completed(self) -> None:
        selected = transaction(
            self.charger,
            "v16-retained-id",
            started_at=self.attempt_at - timedelta(minutes=10),
            stopped_at=self.attempt_at + timedelta(seconds=5),
        )
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="RemoteStopTransaction",
            payload={"transactionId": selected.pk},
        )

        result = reconcile_session_operation(operation)

        self.assertEqual(result.status, ProtocolOperation.Status.COMPLETED)
        self.assertIsNone(result.response_payload)
        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.ACHIEVED,
        )
        self.assertIn(selected.remote_id, result.reconciliation_basis)

    def test_v201_request_stop_settles_when_remote_transaction_is_completed(self) -> None:
        selected = transaction(
            self.charger,
            "remote-201-stop",
            started_at=self.attempt_at - timedelta(minutes=10),
            stopped_at=self.attempt_at + timedelta(seconds=5),
        )
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_201,
            action="RequestStopTransaction",
            payload={"transactionId": selected.remote_id},
        )

        result = reconcile_session_operation(operation)

        self.assertEqual(result.status, ProtocolOperation.Status.COMPLETED)
        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.ACHIEVED,
        )
        self.assertIn(selected.remote_id, result.reconciliation_basis)

    def test_remote_stop_with_open_target_stays_ambiguous(self) -> None:
        selected = transaction(
            self.charger,
            "still-open",
            started_at=self.attempt_at - timedelta(minutes=10),
        )
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_201,
            action="RequestStopTransaction",
            payload={"transactionId": selected.remote_id},
        )

        result = reconcile_session_operation(operation)

        self.assertEqual(
            result.status,
            ProtocolOperation.Status.RECOVERY_REQUIRED,
        )
        self.assertIsNotNone(result.reconciliation_checked_at)
        self.assertIsNone(result.reconciled_at)

    def test_batch_rotates_unresolved_work_instead_of_starving_later_rows(self) -> None:
        first = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="RemoteStartTransaction",
            payload={"idTag": "first"},
        )
        second = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="RemoteStartTransaction",
            payload={"idTag": "second"},
        )

        self.assertEqual(reconcile_session_operations(limit=1), 0)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNotNone(first.reconciliation_checked_at)
        self.assertIsNone(second.reconciliation_checked_at)

        self.assertEqual(reconcile_session_operations(limit=1), 0)
        second.refresh_from_db()
        self.assertIsNotNone(second.reconciliation_checked_at)

    def test_periodic_task_resolves_achieved_session_operation(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="RemoteStartTransaction",
            payload={"idTag": "member-1"},
        )
        transaction(
            self.charger,
            "task-session",
            started_at=self.attempt_at + timedelta(seconds=5),
            id_tag="member-1",
        )

        self.assertEqual(reconcile_ambiguous_session_operations(), 1)

        operation.refresh_from_db()
        self.assertEqual(operation.status, ProtocolOperation.Status.COMPLETED)

    def test_non_reconcile_policy_is_never_settled_by_session_executor(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="RemoteStopTransaction",
            payload={"transactionId": 999},
        )
        operation.recovery_policy = ProtocolOperation.RecoveryPolicy.MANUAL
        operation.save(update_fields=("recovery_policy",))

        result = reconcile_session_operation(operation)

        self.assertEqual(
            result.status,
            ProtocolOperation.Status.RECOVERY_REQUIRED,
        )
        self.assertIsNone(result.reconciliation_checked_at)
        self.assertIsNone(result.reconciled_at)

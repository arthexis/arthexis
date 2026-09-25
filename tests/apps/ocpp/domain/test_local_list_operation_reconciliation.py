from datetime import datetime, timezone

from django.test import TestCase

from apps.ocpp.domain.operations import (
    complete_operation,
    create_operation,
    reconcile_local_list_operation,
)
from apps.ocpp.models import ProtocolOperation
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from tests.apps.ocpp.builders import charger


class LocalListOperationReconciliationTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("reconcile-local-list")
        self.attempt_at = datetime(2026, 9, 24, 20, tzinfo=timezone.utc)

    def _ambiguous(
        self,
        *,
        version: ProtocolVersion,
        payload: dict[str, object],
    ) -> ProtocolOperation:
        operation = create_operation(
            charger=self.charger,
            version=version,
            direction=Direction.CSMS_TO_CHARGE_POINT,
            action="SendLocalList",
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

    def _observe(
        self,
        *,
        version: ProtocolVersion,
        response: dict[str, object] | None = None,
    ) -> ProtocolOperation:
        observation = create_operation(
            charger=self.charger,
            version=version,
            direction=Direction.CSMS_TO_CHARGE_POINT,
            action="GetLocalListVersion",
            request_payload={},
        )
        if response is not None:
            complete_operation(observation, response_payload=response)
        return observation

    def test_v16_matching_version_proves_send_local_list_achieved(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            payload={"listVersion": 7, "updateType": "Full"},
        )
        observation = self._observe(
            version=ProtocolVersion.OCPP_16,
            response={"listVersion": 7},
        )

        result = reconcile_local_list_operation(operation)

        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.ACHIEVED,
        )
        self.assertIn(str(observation.pk), result.reconciliation_basis)
        self.assertEqual(
            ProtocolOperation.objects.filter(action="SendLocalList").count(),
            1,
        )

    def test_v16_mismatching_version_creates_deliberate_replacement(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            payload={"listVersion": 8, "updateType": "Differential"},
        )
        self._observe(
            version=ProtocolVersion.OCPP_16,
            response={"listVersion": 7},
        )

        result = reconcile_local_list_operation(operation)

        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.NOT_ACHIEVED,
        )
        replacement = (
            ProtocolOperation.objects.filter(action="SendLocalList")
            .exclude(pk=operation.pk)
            .get()
        )
        self.assertEqual(replacement.request_payload, operation.request_payload)
        self.assertEqual(replacement.status, ProtocolOperation.Status.PENDING)

    def test_missing_observation_creates_one_safe_query(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            payload={"listVersion": 9, "updateType": "Full"},
        )

        first = reconcile_local_list_operation(operation)
        second = reconcile_local_list_operation(operation)

        self.assertEqual(first.status, ProtocolOperation.Status.RECOVERY_REQUIRED)
        self.assertEqual(second.status, ProtocolOperation.Status.RECOVERY_REQUIRED)
        self.assertEqual(
            ProtocolOperation.objects.filter(action="GetLocalListVersion").count(),
            1,
        )

    def test_pending_observation_leaves_original_ambiguous(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            payload={"listVersion": 10, "updateType": "Full"},
        )
        self._observe(version=ProtocolVersion.OCPP_16)

        result = reconcile_local_list_operation(operation)

        self.assertEqual(result.status, ProtocolOperation.Status.RECOVERY_REQUIRED)
        self.assertIsNone(result.reconciled_at)

    def test_inconclusive_observation_leaves_original_ambiguous(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            payload={"listVersion": 11, "updateType": "Full"},
        )
        self._observe(
            version=ProtocolVersion.OCPP_16,
            response={"unexpected": 11},
        )

        result = reconcile_local_list_operation(operation)

        self.assertEqual(result.status, ProtocolOperation.Status.RECOVERY_REQUIRED)
        self.assertIsNone(result.reconciled_at)

    def test_v201_matching_version_uses_version_number_response(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_201,
            payload={"version": 12, "updateType": "Full"},
        )
        self._observe(
            version=ProtocolVersion.OCPP_201,
            response={"versionNumber": 12},
        )

        result = reconcile_local_list_operation(operation)

        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.ACHIEVED,
        )

    def test_repeated_reconciliation_does_not_duplicate_replacement(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_201,
            payload={"version": 13, "updateType": "Differential"},
        )
        self._observe(
            version=ProtocolVersion.OCPP_201,
            response={"versionNumber": 12},
        )

        first = reconcile_local_list_operation(operation)
        second = reconcile_local_list_operation(operation)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            ProtocolOperation.objects.filter(action="SendLocalList").count(),
            2,
        )

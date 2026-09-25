from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.ocpp.domain.operations import (
    create_operation,
    reconcile_configuration_operation,
)
from apps.ocpp.models import ProtocolOperation
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from apps.ocpp.tasks import reconcile_ambiguous_configuration_operations
from apps.ocpp.transport.operations import deliver_queued_operation
from tests.apps.ocpp.builders import charger


class StaticResponseSender:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response

    async def send(
        self,
        *,
        action: str,
        payload: dict[str, object],
        timeout: float = 30,
        unique_id: str | None = None,
    ) -> dict[str, object]:
        return self.response


class ConfigurationOperationReconciliationTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("reconcile-configuration")
        self.attempt_at = datetime(2026, 9, 24, 20, tzinfo=timezone.utc)

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

    def _complete_observation(
        self,
        *,
        version: ProtocolVersion,
        action: str,
        payload: dict[str, object],
        response: dict[str, object],
    ) -> ProtocolOperation:
        observation = create_operation(
            charger=self.charger,
            version=version,
            direction=Direction.CSMS_TO_CHARGE_POINT,
            action=action,
            request_payload=payload,
        )
        observation.status = ProtocolOperation.Status.COMPLETED
        observation.response_payload = response
        observation.save(update_fields=("status", "response_payload"))
        return observation

    def test_v16_queues_separate_get_configuration_observation(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="ChangeConfiguration",
            payload={"key": "HeartbeatInterval", "value": "300"},
        )

        result = reconcile_configuration_operation(operation)

        self.assertEqual(result.status, ProtocolOperation.Status.RECOVERY_REQUIRED)
        observation = ProtocolOperation.objects.get(action="GetConfiguration")
        self.assertNotEqual(observation.pk, operation.pk)
        self.assertEqual(observation.request_payload, {"key": ["HeartbeatInterval"]})
        self.assertEqual(
            observation.recovery_policy,
            ProtocolOperation.RecoveryPolicy.SAFE_RETRY,
        )

    def test_v16_matching_observation_settles_original_as_achieved(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="ChangeConfiguration",
            payload={"key": "HeartbeatInterval", "value": "300"},
        )
        observation = self._complete_observation(
            version=ProtocolVersion.OCPP_16,
            action="GetConfiguration",
            payload={"key": ["HeartbeatInterval"]},
            response={
                "configurationKey": [
                    {
                        "key": "HeartbeatInterval",
                        "readonly": False,
                        "value": "300",
                    }
                ]
            },
        )

        result = reconcile_configuration_operation(operation)

        self.assertEqual(result.status, ProtocolOperation.Status.COMPLETED)
        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.ACHIEVED,
        )
        self.assertIn(str(observation.pk), result.reconciliation_basis)
        self.assertIsNone(result.response_payload)
        self.assertEqual(
            ProtocolOperation.objects.filter(action="ChangeConfiguration").count(),
            1,
        )

    def test_v16_mismatch_creates_new_deliberate_mutation(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="ChangeConfiguration",
            payload={"key": "HeartbeatInterval", "value": "300"},
        )
        self._complete_observation(
            version=ProtocolVersion.OCPP_16,
            action="GetConfiguration",
            payload={"key": ["HeartbeatInterval"]},
            response={
                "configurationKey": [
                    {
                        "key": "HeartbeatInterval",
                        "readonly": False,
                        "value": "60",
                    }
                ]
            },
        )

        result = reconcile_configuration_operation(operation)

        self.assertEqual(result.status, ProtocolOperation.Status.COMPLETED)
        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.NOT_ACHIEVED,
        )
        replacement = (
            ProtocolOperation.objects.filter(action="ChangeConfiguration")
            .exclude(pk=operation.pk)
            .get()
        )
        self.assertEqual(replacement.status, ProtocolOperation.Status.PENDING)
        self.assertEqual(replacement.request_payload, operation.request_payload)
        self.assertEqual(
            replacement.recovery_policy,
            ProtocolOperation.RecoveryPolicy.RECONCILE,
        )
        self.assertIn(str(replacement.pk), result.reconciliation_basis)

    def test_v201_set_variables_queues_matching_get_variables_observation(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_201,
            action="SetVariables",
            payload={
                "setVariableData": [
                    {
                        "component": {"name": "TxCtrlr"},
                        "variable": {"name": "EVConnectionTimeOut"},
                        "attributeType": "Actual",
                        "attributeValue": "30",
                    }
                ]
            },
        )

        reconcile_configuration_operation(operation)

        observation = ProtocolOperation.objects.get(action="GetVariables")
        self.assertEqual(
            observation.request_payload,
            {
                "getVariableData": [
                    {
                        "component": {"name": "TxCtrlr"},
                        "variable": {"name": "EVConnectionTimeOut"},
                        "attributeType": "Actual",
                    }
                ]
            },
        )

    def test_v201_matching_observation_settles_set_variables(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_201,
            action="SetVariables",
            payload={
                "setVariableData": [
                    {
                        "component": {"name": "TxCtrlr"},
                        "variable": {"name": "EVConnectionTimeOut"},
                        "attributeType": "Actual",
                        "attributeValue": "30",
                    }
                ]
            },
        )
        self._complete_observation(
            version=ProtocolVersion.OCPP_201,
            action="GetVariables",
            payload={
                "getVariableData": [
                    {
                        "component": {"name": "TxCtrlr"},
                        "variable": {"name": "EVConnectionTimeOut"},
                        "attributeType": "Actual",
                    }
                ]
            },
            response={
                "getVariableResult": [
                    {
                        "attributeStatus": "Accepted",
                        "attributeType": "Actual",
                        "attributeValue": "30",
                        "component": {"name": "TxCtrlr"},
                        "variable": {"name": "EVConnectionTimeOut"},
                    }
                ]
            },
        )

        result = reconcile_configuration_operation(operation)

        self.assertEqual(result.status, ProtocolOperation.Status.COMPLETED)
        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.ACHIEVED,
        )

    def test_incomplete_observation_keeps_original_ambiguous(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_201,
            action="SetVariables",
            payload={
                "setVariableData": [
                    {
                        "component": {"name": "TxCtrlr"},
                        "variable": {"name": "EVConnectionTimeOut"},
                        "attributeValue": "30",
                    }
                ]
            },
        )
        self._complete_observation(
            version=ProtocolVersion.OCPP_201,
            action="GetVariables",
            payload={
                "getVariableData": [
                    {
                        "component": {"name": "TxCtrlr"},
                        "variable": {"name": "EVConnectionTimeOut"},
                    }
                ]
            },
            response={"getVariableResult": []},
        )

        result = reconcile_configuration_operation(operation)

        self.assertEqual(result.status, ProtocolOperation.Status.RECOVERY_REQUIRED)
        self.assertIsNone(result.reconciled_at)
        self.assertEqual(result.reconciliation_resolution, "")

    def test_periodic_task_queues_configuration_observation(self) -> None:
        self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="ChangeConfiguration",
            payload={"key": "HeartbeatInterval", "value": "300"},
        )

        with patch(
            "apps.ocpp.tasks.enqueue_existing_operation",
            new_callable=AsyncMock,
            return_value=True,
        ) as enqueue:
            self.assertEqual(reconcile_ambiguous_configuration_operations(), 0)

        observation = ProtocolOperation.objects.get(action="GetConfiguration")
        enqueue.assert_awaited_once()
        self.assertEqual(enqueue.await_args.args[0].pk, observation.pk)

    def test_repeated_sweeps_reuse_one_pending_observation(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="ChangeConfiguration",
            payload={"key": "HeartbeatInterval", "value": "300"},
        )

        reconcile_configuration_operation(operation)
        reconcile_configuration_operation(operation)

        observations = ProtocolOperation.objects.filter(action="GetConfiguration")
        self.assertEqual(observations.count(), 1)
        self.assertEqual(observations.get().status, ProtocolOperation.Status.PENDING)


    def test_delivered_observation_resolves_original_on_next_pass(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="ChangeConfiguration",
            payload={"key": "HeartbeatInterval", "value": "300"},
        )

        reconcile_configuration_operation(operation)
        observation = ProtocolOperation.objects.get(action="GetConfiguration")

        async_to_sync(deliver_queued_operation)(
            charger=self.charger,
            sender=StaticResponseSender(
                {
                    "configurationKey": [
                        {
                            "key": "HeartbeatInterval",
                            "readonly": False,
                            "value": "300",
                        }
                    ]
                }
            ),
            version=ProtocolVersion.OCPP_16,
            operation_id=observation.pk,
            timeout=30,
            delivery_owner="test.consumer",
        )

        observation.refresh_from_db()
        self.assertEqual(observation.status, ProtocolOperation.Status.COMPLETED)
        self.assertEqual(observation.attempt_count, 1)

        result = reconcile_configuration_operation(operation)

        self.assertEqual(result.status, ProtocolOperation.Status.COMPLETED)
        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.ACHIEVED,
        )
        self.assertIn(str(observation.pk), result.reconciliation_basis)

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from asgiref.sync import async_to_sync

from apps.ocpp.domain.operations import reconcile_configuration_operation
from apps.ocpp.models import ProtocolOperation
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.tasks import reconcile_ambiguous_configuration_operations
from apps.ocpp.transport.operations import deliver_queued_operation
from tests.apps.ocpp.builders import charger, protocol_operation

pytestmark = pytest.mark.django_db


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


@pytest.fixture
def configuration_context():
    selected = charger("reconcile-configuration")
    attempt_at = datetime(2026, 9, 24, 20, tzinfo=timezone.utc)

    def ambiguous(*, version: ProtocolVersion, action: str, payload: dict[str, object]):
        return protocol_operation(
            selected,
            action,
            version=version,
            request_payload=payload,
            status=ProtocolOperation.Status.RECOVERY_REQUIRED,
            attempts=1,
            attempt_at=attempt_at,
        )

    def complete_observation(
        *,
        version: ProtocolVersion,
        action: str,
        payload: dict[str, object],
        response: dict[str, object],
    ):
        return protocol_operation(
            selected,
            action,
            version=version,
            request_payload=payload,
            status=ProtocolOperation.Status.COMPLETED,
            response_payload=response,
        )

    return selected, ambiguous, complete_observation


def test_v16_queues_separate_get_configuration_observation(configuration_context) -> None:
    _, ambiguous, _ = configuration_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="ChangeConfiguration",
        payload={"key": "HeartbeatInterval", "value": "300"},
    )

    result = reconcile_configuration_operation(operation)

    assert result.status == ProtocolOperation.Status.RECOVERY_REQUIRED
    observation = ProtocolOperation.objects.get(action="GetConfiguration")
    assert observation.pk != operation.pk
    assert observation.request_payload == {"key": ["HeartbeatInterval"]}
    assert observation.recovery_policy == ProtocolOperation.RecoveryPolicy.SAFE_RETRY


def test_v16_matching_observation_settles_original_as_achieved(configuration_context) -> None:
    _, ambiguous, complete = configuration_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="ChangeConfiguration",
        payload={"key": "HeartbeatInterval", "value": "300"},
    )
    observation = complete(
        version=ProtocolVersion.OCPP_16,
        action="GetConfiguration",
        payload={"key": ["HeartbeatInterval"]},
        response={"configurationKey": [{"key": "HeartbeatInterval", "readonly": False, "value": "300"}]},
    )

    result = reconcile_configuration_operation(operation)

    assert result.status == ProtocolOperation.Status.COMPLETED
    assert result.reconciliation_resolution == ProtocolOperation.ReconciliationResolution.ACHIEVED
    assert str(observation.pk) in result.reconciliation_basis
    assert result.response_payload is None
    assert ProtocolOperation.objects.filter(action="ChangeConfiguration").count() == 1


def test_v16_mismatch_creates_new_deliberate_mutation(configuration_context) -> None:
    _, ambiguous, complete = configuration_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="ChangeConfiguration",
        payload={"key": "HeartbeatInterval", "value": "300"},
    )
    complete(
        version=ProtocolVersion.OCPP_16,
        action="GetConfiguration",
        payload={"key": ["HeartbeatInterval"]},
        response={"configurationKey": [{"key": "HeartbeatInterval", "readonly": False, "value": "60"}]},
    )

    result = reconcile_configuration_operation(operation)

    assert result.status == ProtocolOperation.Status.COMPLETED
    assert result.reconciliation_resolution == ProtocolOperation.ReconciliationResolution.NOT_ACHIEVED
    replacement = ProtocolOperation.objects.filter(action="ChangeConfiguration").exclude(pk=operation.pk).get()
    assert replacement.status == ProtocolOperation.Status.PENDING
    assert replacement.request_payload == operation.request_payload
    assert replacement.recovery_policy == ProtocolOperation.RecoveryPolicy.RECONCILE
    assert str(replacement.pk) in result.reconciliation_basis


def test_v201_set_variables_queues_matching_get_variables_observation(configuration_context) -> None:
    _, ambiguous, _ = configuration_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_201,
        action="SetVariables",
        payload={"setVariableData": [{"component": {"name": "TxCtrlr"}, "variable": {"name": "EVConnectionTimeOut"}, "attributeType": "Actual", "attributeValue": "30"}]},
    )

    reconcile_configuration_operation(operation)

    observation = ProtocolOperation.objects.get(action="GetVariables")
    assert observation.request_payload == {
        "getVariableData": [{"component": {"name": "TxCtrlr"}, "variable": {"name": "EVConnectionTimeOut"}, "attributeType": "Actual"}]
    }


def test_v201_matching_observation_settles_set_variables(configuration_context) -> None:
    _, ambiguous, complete = configuration_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_201,
        action="SetVariables",
        payload={"setVariableData": [{"component": {"name": "TxCtrlr"}, "variable": {"name": "EVConnectionTimeOut"}, "attributeType": "Actual", "attributeValue": "30"}]},
    )
    complete(
        version=ProtocolVersion.OCPP_201,
        action="GetVariables",
        payload={"getVariableData": [{"component": {"name": "TxCtrlr"}, "variable": {"name": "EVConnectionTimeOut"}, "attributeType": "Actual"}]},
        response={"getVariableResult": [{"attributeStatus": "Accepted", "attributeType": "Actual", "attributeValue": "30", "component": {"name": "TxCtrlr"}, "variable": {"name": "EVConnectionTimeOut"}}]},
    )

    result = reconcile_configuration_operation(operation)

    assert result.status == ProtocolOperation.Status.COMPLETED
    assert result.reconciliation_resolution == ProtocolOperation.ReconciliationResolution.ACHIEVED


def test_incomplete_observation_keeps_original_ambiguous(configuration_context) -> None:
    _, ambiguous, complete = configuration_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_201,
        action="SetVariables",
        payload={"setVariableData": [{"component": {"name": "TxCtrlr"}, "variable": {"name": "EVConnectionTimeOut"}, "attributeValue": "30"}]},
    )
    complete(
        version=ProtocolVersion.OCPP_201,
        action="GetVariables",
        payload={"getVariableData": [{"component": {"name": "TxCtrlr"}, "variable": {"name": "EVConnectionTimeOut"}}]},
        response={"getVariableResult": []},
    )

    result = reconcile_configuration_operation(operation)

    assert result.status == ProtocolOperation.Status.RECOVERY_REQUIRED
    assert result.reconciled_at is None
    assert result.reconciliation_resolution == ""


def test_periodic_task_queues_configuration_observation(configuration_context) -> None:
    _, ambiguous, _ = configuration_context
    ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="ChangeConfiguration",
        payload={"key": "HeartbeatInterval", "value": "300"},
    )

    with patch(
        "apps.ocpp.tasks.enqueue_existing_operation",
        new_callable=AsyncMock,
        return_value=True,
    ) as enqueue:
        assert reconcile_ambiguous_configuration_operations() == 0

    observation = ProtocolOperation.objects.get(action="GetConfiguration")
    enqueue.assert_awaited_once()
    assert enqueue.await_args.args[0].pk == observation.pk


def test_repeated_sweeps_reuse_one_pending_observation(configuration_context) -> None:
    _, ambiguous, _ = configuration_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="ChangeConfiguration",
        payload={"key": "HeartbeatInterval", "value": "300"},
    )

    reconcile_configuration_operation(operation)
    reconcile_configuration_operation(operation)

    observations = ProtocolOperation.objects.filter(action="GetConfiguration")
    assert observations.count() == 1
    assert observations.get().status == ProtocolOperation.Status.PENDING


def test_delivered_observation_resolves_original_on_next_pass(configuration_context) -> None:
    selected, ambiguous, _ = configuration_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="ChangeConfiguration",
        payload={"key": "HeartbeatInterval", "value": "300"},
    )

    reconcile_configuration_operation(operation)
    observation = ProtocolOperation.objects.get(action="GetConfiguration")

    async_to_sync(deliver_queued_operation)(
        charger=selected,
        sender=StaticResponseSender(
            {"configurationKey": [{"key": "HeartbeatInterval", "readonly": False, "value": "300"}]}
        ),
        version=ProtocolVersion.OCPP_16,
        operation_id=observation.pk,
        timeout=30,
        delivery_owner="test.consumer",
    )

    observation.refresh_from_db()
    assert observation.status == ProtocolOperation.Status.COMPLETED
    assert observation.attempt_count == 1

    result = reconcile_configuration_operation(operation)

    assert result.status == ProtocolOperation.Status.COMPLETED
    assert result.reconciliation_resolution == ProtocolOperation.ReconciliationResolution.ACHIEVED
    assert str(observation.pk) in result.reconciliation_basis

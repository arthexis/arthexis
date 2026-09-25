import pytest

from apps.ocpp.domain.operations import (
    create_operation,
    prepare_safe_retry,
    require_operation_recovery,
)
from apps.ocpp.models import ProtocolOperation
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from apps.ocpp.protocol.recovery import (
    MANUAL,
    RECONCILE,
    SAFE_RETRY,
    recovery_policy_for,
)
from apps.ocpp.protocol.v16.matrix import OUTBOUND_ACTIONS as V16_OUTBOUND_ACTIONS
from apps.ocpp.protocol.v201.matrix import OUTBOUND_ACTIONS as V201_OUTBOUND_ACTIONS
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


@pytest.fixture
def recovery_charger():
    return charger("recovery-policy")


@pytest.fixture
def operation_factory(recovery_charger):
    def create(*, version: ProtocolVersion, action: str) -> ProtocolOperation:
        return create_operation(
            charger=recovery_charger,
            version=version,
            direction=Direction.CSMS_TO_CHARGE_POINT,
            action=action,
            request_payload={},
        )

    return create


def test_every_outbound_action_has_a_supported_policy() -> None:
    allowed = {SAFE_RETRY, RECONCILE, MANUAL}
    for contract in (*V16_OUTBOUND_ACTIONS, *V201_OUTBOUND_ACTIONS):
        assert recovery_policy_for(
            version=contract.version,
            action=contract.action,
        ) in allowed


@pytest.mark.parametrize(
    ("version", "action"),
    [
        (ProtocolVersion.OCPP_16, "GetConfiguration"),
        (ProtocolVersion.OCPP_16, "GetCompositeSchedule"),
        (ProtocolVersion.OCPP_16, "GetLocalListVersion"),
        (ProtocolVersion.OCPP_201, "GetVariables"),
        (ProtocolVersion.OCPP_201, "GetCompositeSchedule"),
        (ProtocolVersion.OCPP_201, "GetInstalledCertificateIds"),
    ],
)
def test_safe_retry_is_restricted_to_observational_requests(
    version: ProtocolVersion,
    action: str,
) -> None:
    assert recovery_policy_for(version=version, action=action) == SAFE_RETRY


@pytest.mark.parametrize(
    ("version", "action"),
    [
        (ProtocolVersion.OCPP_16, "RemoteStartTransaction"),
        (ProtocolVersion.OCPP_16, "RemoteStopTransaction"),
        (ProtocolVersion.OCPP_16, "ChangeAvailability"),
        (ProtocolVersion.OCPP_16, "ReserveNow"),
        (ProtocolVersion.OCPP_16, "SetChargingProfile"),
        (ProtocolVersion.OCPP_201, "RequestStartTransaction"),
        (ProtocolVersion.OCPP_201, "RequestStopTransaction"),
        (ProtocolVersion.OCPP_201, "ChangeAvailability"),
        (ProtocolVersion.OCPP_201, "SetVariables"),
        (ProtocolVersion.OCPP_201, "InstallCertificate"),
    ],
)
def test_state_changing_actions_require_reconciliation(
    version: ProtocolVersion,
    action: str,
) -> None:
    assert recovery_policy_for(version=version, action=action) == RECONCILE


@pytest.mark.parametrize(
    ("version", "action"),
    [
        (ProtocolVersion.OCPP_16, "DataTransfer"),
        (ProtocolVersion.OCPP_16, "Reset"),
        (ProtocolVersion.OCPP_16, "UnlockConnector"),
        (ProtocolVersion.OCPP_16, "UpdateFirmware"),
        (ProtocolVersion.OCPP_201, "DataTransfer"),
        (ProtocolVersion.OCPP_201, "Reset"),
        (ProtocolVersion.OCPP_201, "UnlockConnector"),
        (ProtocolVersion.OCPP_201, "PublishFirmware"),
        (ProtocolVersion.OCPP_201, "UpdateFirmware"),
    ],
)
def test_opaque_or_consequential_actions_are_manual(
    version: ProtocolVersion,
    action: str,
) -> None:
    assert recovery_policy_for(version=version, action=action) == MANUAL


def test_new_operation_persists_its_policy_snapshot(operation_factory) -> None:
    safe = operation_factory(
        version=ProtocolVersion.OCPP_16,
        action="GetConfiguration",
    )
    reconcile = operation_factory(
        version=ProtocolVersion.OCPP_16,
        action="RemoteStopTransaction",
    )
    manual = operation_factory(
        version=ProtocolVersion.OCPP_16,
        action="Reset",
    )

    assert safe.recovery_policy == ProtocolOperation.RecoveryPolicy.SAFE_RETRY
    assert reconcile.recovery_policy == ProtocolOperation.RecoveryPolicy.RECONCILE
    assert manual.recovery_policy == ProtocolOperation.RecoveryPolicy.MANUAL


def test_safe_retry_can_return_ambiguous_operation_to_pending(operation_factory) -> None:
    operation = operation_factory(
        version=ProtocolVersion.OCPP_16,
        action="GetConfiguration",
    )
    operation.status = ProtocolOperation.Status.DELIVERING
    operation.attempt_count = 1
    operation.save(update_fields=("status", "attempt_count"))
    require_operation_recovery(operation, description="response lost")

    recovered = prepare_safe_retry(operation)

    assert recovered is not None
    operation.refresh_from_db()
    assert operation.status == ProtocolOperation.Status.PENDING
    assert operation.attempt_count == 1
    assert operation.completed_at is None


@pytest.mark.parametrize("action", ["RemoteStopTransaction", "Reset"])
def test_non_safe_retry_policies_cannot_be_blindly_retried(
    operation_factory,
    action: str,
) -> None:
    operation = operation_factory(
        version=ProtocolVersion.OCPP_16,
        action=action,
    )
    operation.status = ProtocolOperation.Status.DELIVERING
    operation.save(update_fields=("status",))
    require_operation_recovery(operation, description="connection lost")

    assert prepare_safe_retry(operation) is None
    operation.refresh_from_db()
    assert operation.status == ProtocolOperation.Status.RECOVERY_REQUIRED

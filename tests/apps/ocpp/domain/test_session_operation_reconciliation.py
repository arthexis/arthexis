from datetime import datetime, timedelta, timezone

import pytest

from apps.ocpp.domain.operations import (
    reconcile_session_operation,
    reconcile_session_operations,
)
from apps.ocpp.models import ProtocolOperation
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.tasks import reconcile_ambiguous_session_operations
from tests.apps.ocpp.builders import charger, connector, protocol_operation, transaction

pytestmark = pytest.mark.django_db


@pytest.fixture
def session_reconciliation_context():
    selected = charger("reconcile-session")
    attempt_at = datetime(2026, 9, 23, 10, tzinfo=timezone.utc)

    def ambiguous(
        *,
        version: ProtocolVersion,
        action: str,
        payload: dict[str, object],
    ) -> ProtocolOperation:
        return protocol_operation(
            selected,
            action,
            version=version,
            request_payload=payload,
            status=ProtocolOperation.Status.RECOVERY_REQUIRED,
            attempts=1,
            attempt_at=attempt_at,
        )

    return selected, attempt_at, ambiguous


def test_v16_remote_start_settles_from_matching_later_transaction(
    session_reconciliation_context,
) -> None:
    selected_charger, attempt_at, ambiguous = session_reconciliation_context
    selected_connector = connector(selected_charger, number=1)
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="RemoteStartTransaction",
        payload={"idTag": "member-1", "connectorId": 1},
    )
    selected = transaction(
        selected_charger,
        "started-after-ambiguity",
        started_at=attempt_at + timedelta(seconds=5),
        connector=selected_connector,
        id_tag="member-1",
    )

    result = reconcile_session_operation(operation)

    assert result.status == ProtocolOperation.Status.COMPLETED
    assert result.response_payload is None
    assert result.reconciled_at is not None
    assert (
        result.reconciliation_resolution
        == ProtocolOperation.ReconciliationResolution.ACHIEVED
    )
    assert selected.remote_id in result.reconciliation_basis
    assert result.reconciliation_checked_at is not None


def test_v201_request_start_settles_from_matching_evse_transaction(
    session_reconciliation_context,
) -> None:
    selected_charger, attempt_at, ambiguous = session_reconciliation_context
    selected_connector = connector(selected_charger, number=3001)
    operation = ambiguous(
        version=ProtocolVersion.OCPP_201,
        action="RequestStartTransaction",
        payload={
            "idToken": {"idToken": "member-2"},
            "remoteStartId": 42,
            "evseId": 3,
        },
    )
    selected = transaction(
        selected_charger,
        "remote-201",
        started_at=attempt_at + timedelta(seconds=10),
        connector=selected_connector,
        id_tag="member-2",
    )

    result = reconcile_session_operation(operation)

    assert result.status == ProtocolOperation.Status.COMPLETED
    assert (
        result.reconciliation_resolution
        == ProtocolOperation.ReconciliationResolution.ACHIEVED
    )
    assert selected.remote_id in result.reconciliation_basis


def test_remote_start_without_positive_session_evidence_stays_ambiguous(
    session_reconciliation_context,
) -> None:
    _, _, ambiguous = session_reconciliation_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="RemoteStartTransaction",
        payload={"idTag": "member-1"},
    )

    result = reconcile_session_operation(operation)

    assert result.status == ProtocolOperation.Status.RECOVERY_REQUIRED
    assert result.reconciliation_checked_at is not None
    assert result.reconciled_at is None
    assert result.reconciliation_resolution == ""
    assert result.reconciliation_basis == ""


def test_transaction_before_remote_start_attempt_does_not_prove_achievement(
    session_reconciliation_context,
) -> None:
    selected_charger, attempt_at, ambiguous = session_reconciliation_context
    transaction(
        selected_charger,
        "older-session",
        started_at=attempt_at - timedelta(seconds=1),
        id_tag="member-1",
    )
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="RemoteStartTransaction",
        payload={"idTag": "member-1"},
    )

    result = reconcile_session_operation(operation)

    assert result.status == ProtocolOperation.Status.RECOVERY_REQUIRED


def test_v16_remote_stop_settles_when_server_transaction_is_completed(
    session_reconciliation_context,
) -> None:
    selected_charger, attempt_at, ambiguous = session_reconciliation_context
    selected = transaction(
        selected_charger,
        "v16-retained-id",
        started_at=attempt_at - timedelta(minutes=10),
        stopped_at=attempt_at + timedelta(seconds=5),
    )
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="RemoteStopTransaction",
        payload={"transactionId": selected.pk},
    )

    result = reconcile_session_operation(operation)

    assert result.status == ProtocolOperation.Status.COMPLETED
    assert result.response_payload is None
    assert (
        result.reconciliation_resolution
        == ProtocolOperation.ReconciliationResolution.ACHIEVED
    )
    assert selected.remote_id in result.reconciliation_basis


def test_v201_request_stop_settles_when_remote_transaction_is_completed(
    session_reconciliation_context,
) -> None:
    selected_charger, attempt_at, ambiguous = session_reconciliation_context
    selected = transaction(
        selected_charger,
        "remote-201-stop",
        started_at=attempt_at - timedelta(minutes=10),
        stopped_at=attempt_at + timedelta(seconds=5),
    )
    operation = ambiguous(
        version=ProtocolVersion.OCPP_201,
        action="RequestStopTransaction",
        payload={"transactionId": selected.remote_id},
    )

    result = reconcile_session_operation(operation)

    assert result.status == ProtocolOperation.Status.COMPLETED
    assert (
        result.reconciliation_resolution
        == ProtocolOperation.ReconciliationResolution.ACHIEVED
    )
    assert selected.remote_id in result.reconciliation_basis


def test_remote_stop_with_open_target_stays_ambiguous(
    session_reconciliation_context,
) -> None:
    selected_charger, attempt_at, ambiguous = session_reconciliation_context
    selected = transaction(
        selected_charger,
        "still-open",
        started_at=attempt_at - timedelta(minutes=10),
    )
    operation = ambiguous(
        version=ProtocolVersion.OCPP_201,
        action="RequestStopTransaction",
        payload={"transactionId": selected.remote_id},
    )

    result = reconcile_session_operation(operation)

    assert result.status == ProtocolOperation.Status.RECOVERY_REQUIRED
    assert result.reconciliation_checked_at is not None
    assert result.reconciled_at is None


def test_batch_rotates_unresolved_work_instead_of_starving_later_rows(
    session_reconciliation_context,
) -> None:
    _, _, ambiguous = session_reconciliation_context
    first = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="RemoteStartTransaction",
        payload={"idTag": "first"},
    )
    second = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="RemoteStartTransaction",
        payload={"idTag": "second"},
    )

    assert reconcile_session_operations(limit=1) == 0
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.reconciliation_checked_at is not None
    assert second.reconciliation_checked_at is None

    assert reconcile_session_operations(limit=1) == 0
    second.refresh_from_db()
    assert second.reconciliation_checked_at is not None


def test_periodic_task_resolves_achieved_session_operation(
    session_reconciliation_context,
) -> None:
    selected_charger, attempt_at, ambiguous = session_reconciliation_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="RemoteStartTransaction",
        payload={"idTag": "member-1"},
    )
    transaction(
        selected_charger,
        "task-session",
        started_at=attempt_at + timedelta(seconds=5),
        id_tag="member-1",
    )

    assert reconcile_ambiguous_session_operations() == 1

    operation.refresh_from_db()
    assert operation.status == ProtocolOperation.Status.COMPLETED


def test_non_reconcile_policy_is_never_settled_by_session_executor(
    session_reconciliation_context,
) -> None:
    _, _, ambiguous = session_reconciliation_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="RemoteStopTransaction",
        payload={"transactionId": 999},
    )
    operation.recovery_policy = ProtocolOperation.RecoveryPolicy.MANUAL
    operation.save(update_fields=("recovery_policy",))

    result = reconcile_session_operation(operation)

    assert result.status == ProtocolOperation.Status.RECOVERY_REQUIRED
    assert result.reconciliation_checked_at is None
    assert result.reconciled_at is None

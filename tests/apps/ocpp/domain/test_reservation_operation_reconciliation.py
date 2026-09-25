from datetime import datetime, timedelta, timezone

import pytest

from apps.ocpp.domain.operations import reconcile_reservation_operation
from apps.ocpp.domain.reservations import record_reservation
from apps.ocpp.models import Connector, ProtocolOperation
from apps.ocpp.protocol.contracts import ProtocolVersion
from tests.apps.ocpp.builders import charger, protocol_operation

pytestmark = pytest.mark.django_db


@pytest.fixture
def reservation_context():
    selected = charger("reconcile-reservation")
    attempt_at = datetime(2026, 9, 24, 20, tzinfo=timezone.utc)

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

    def reservation(
        *,
        remote_id: str,
        id_tag: str = "card-1",
        status: str = "pending",
        connector_number: int | None = None,
        updated_at: datetime | None = None,
    ):
        selected_connector = None
        if connector_number is not None:
            selected_connector = Connector.objects.create(
                charger=selected,
                number=connector_number,
            )
        value = record_reservation(
            charger=selected,
            remote_id=remote_id,
            id_tag=id_tag,
            expires_at=attempt_at + timedelta(hours=1),
            connector=selected_connector,
            status=status,
        )
        if updated_at is not None:
            value.__class__.objects.filter(pk=value.pk).update(updated_at=updated_at)
            value.refresh_from_db()
        return value

    return attempt_at, ambiguous, reservation


def test_v16_fresh_matching_reservation_proves_reserve_now_achieved(
    reservation_context,
) -> None:
    attempt_at, ambiguous, reservation = reservation_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="ReserveNow",
        payload={
            "connectorId": 1,
            "expiryDate": "2099-09-24T21:00:00Z",
            "idTag": "card-1",
            "reservationId": 41,
        },
    )
    value = reservation(
        remote_id="41",
        connector_number=1,
        updated_at=attempt_at + timedelta(seconds=1),
    )

    result = reconcile_reservation_operation(operation)

    assert result.status == ProtocolOperation.Status.COMPLETED
    assert (
        result.reconciliation_resolution
        == ProtocolOperation.ReconciliationResolution.ACHIEVED
    )
    assert value.remote_id in result.reconciliation_basis
    assert ProtocolOperation.objects.filter(action="ReserveNow").count() == 1


def test_v16_terminal_reservation_creates_deliberate_reserve_replacement(
    reservation_context,
) -> None:
    attempt_at, ambiguous, reservation = reservation_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="ReserveNow",
        payload={
            "connectorId": 1,
            "expiryDate": "2099-09-24T21:00:00Z",
            "idTag": "card-1",
            "reservationId": 42,
        },
    )
    reservation(
        remote_id="42",
        status="rejected",
        connector_number=1,
        updated_at=attempt_at + timedelta(seconds=1),
    )

    result = reconcile_reservation_operation(operation)

    assert (
        result.reconciliation_resolution
        == ProtocolOperation.ReconciliationResolution.NOT_ACHIEVED
    )
    replacement = (
        ProtocolOperation.objects.filter(action="ReserveNow")
        .exclude(pk=operation.pk)
        .get()
    )
    assert replacement.request_payload == operation.request_payload
    assert replacement.status == ProtocolOperation.Status.PENDING


def test_fresh_terminal_state_proves_cancel_reservation_achieved(
    reservation_context,
) -> None:
    attempt_at, ambiguous, reservation = reservation_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="CancelReservation",
        payload={"reservationId": 43},
    )
    reservation(
        remote_id="43",
        status="cancelled",
        updated_at=attempt_at + timedelta(seconds=1),
    )

    result = reconcile_reservation_operation(operation)

    assert (
        result.reconciliation_resolution
        == ProtocolOperation.ReconciliationResolution.ACHIEVED
    )
    assert ProtocolOperation.objects.filter(action="CancelReservation").count() == 1


def test_fresh_active_state_creates_deliberate_cancel_replacement(
    reservation_context,
) -> None:
    attempt_at, ambiguous, reservation = reservation_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="CancelReservation",
        payload={"reservationId": 44},
    )
    reservation(
        remote_id="44",
        status="accepted",
        updated_at=attempt_at + timedelta(seconds=1),
    )

    result = reconcile_reservation_operation(operation)

    assert (
        result.reconciliation_resolution
        == ProtocolOperation.ReconciliationResolution.NOT_ACHIEVED
    )
    replacement = (
        ProtocolOperation.objects.filter(action="CancelReservation")
        .exclude(pk=operation.pk)
        .get()
    )
    assert replacement.status == ProtocolOperation.Status.PENDING


def test_stale_reservation_state_does_not_resolve_ambiguity(
    reservation_context,
) -> None:
    attempt_at, ambiguous, reservation = reservation_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="CancelReservation",
        payload={"reservationId": 45},
    )
    reservation(
        remote_id="45",
        status="cancelled",
        updated_at=attempt_at - timedelta(seconds=1),
    )

    result = reconcile_reservation_operation(operation)

    assert result.status == ProtocolOperation.Status.RECOVERY_REQUIRED
    assert result.reconciled_at is None


def test_v201_reservation_identity_uses_id_token_and_evse(
    reservation_context,
) -> None:
    attempt_at, ambiguous, reservation = reservation_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_201,
        action="ReserveNow",
        payload={
            "id": 46,
            "expiryDate": "2099-09-24T21:00:00Z",
            "idToken": {"idToken": "card-2", "type": "Central"},
            "evseId": 2,
        },
    )
    reservation(
        remote_id="46",
        id_tag="card-2",
        connector_number=2001,
        updated_at=attempt_at + timedelta(seconds=1),
    )

    result = reconcile_reservation_operation(operation)

    assert (
        result.reconciliation_resolution
        == ProtocolOperation.ReconciliationResolution.ACHIEVED
    )


def test_repeated_sweep_does_not_create_duplicate_replacement(
    reservation_context,
) -> None:
    attempt_at, ambiguous, reservation = reservation_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="CancelReservation",
        payload={"reservationId": 47},
    )
    reservation(
        remote_id="47",
        status="active",
        updated_at=attempt_at + timedelta(seconds=1),
    )

    first = reconcile_reservation_operation(operation)
    second = reconcile_reservation_operation(operation)

    assert first.pk == second.pk
    assert ProtocolOperation.objects.filter(action="CancelReservation").count() == 2


def test_expired_reserve_now_does_not_create_replacement(
    reservation_context,
) -> None:
    attempt_at, ambiguous, reservation = reservation_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="ReserveNow",
        payload={
            "connectorId": 1,
            "expiryDate": "2026-09-24T19:00:00Z",
            "idTag": "card-1",
            "reservationId": 48,
        },
    )
    reservation(
        remote_id="48",
        status="rejected",
        connector_number=1,
        updated_at=attempt_at + timedelta(seconds=1),
    )

    result = reconcile_reservation_operation(operation)

    assert (
        result.reconciliation_resolution
        == ProtocolOperation.ReconciliationResolution.NOT_ACHIEVED
    )
    assert ProtocolOperation.objects.filter(action="ReserveNow").count() == 1
    assert "no replacement operation was created" in result.reconciliation_basis

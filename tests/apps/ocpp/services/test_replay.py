from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.ocpp.models import InboundProtocolRequest
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.frames import CallError, CallResult
from apps.ocpp.protocol.replay import ReplayPolicy
from apps.ocpp.services.replay import (
    acquire_inbound_request,
    complete_with_error,
    complete_with_result,
    stored_response,
)
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


@pytest.fixture
def replay_service_charger():
    return charger("charger-replay-service")


@pytest.fixture
def acquire(replay_service_charger):
    def acquire_request(
        *,
        call_id: str = "call-1",
        payload: dict[str, object] | None = None,
        policy: ReplayPolicy = ReplayPolicy.CALL_ID_AND_FINGERPRINT,
        domain_identity: str = "",
    ):
        return acquire_inbound_request(
            charger=replay_service_charger,
            version=ProtocolVersion.OCPP_16,
            action="DataTransfer",
            call_id=call_id,
            payload=payload or {"vendorId": "vendor"},
            policy=policy,
            domain_identity=domain_identity,
        )

    return acquire_request


def test_first_acquisition_creates_processing_request(acquire) -> None:
    acquired = acquire()

    assert acquired.created
    assert not acquired.completed
    assert acquired.request.status == InboundProtocolRequest.Status.PROCESSING


def test_repeat_acquisition_recovers_same_request(acquire) -> None:
    first = acquire()
    second = acquire()

    assert not second.created
    assert second.request.pk == first.request.pk


def test_reused_call_id_with_changed_payload_creates_distinct_request(acquire) -> None:
    first = acquire(payload={"vendorId": "vendor", "data": "one"})
    second = acquire(payload={"vendorId": "vendor", "data": "two"})

    assert first.created
    assert second.created
    assert first.request.pk != second.request.pk


def test_domain_identity_recovers_request_across_call_ids(acquire) -> None:
    first = acquire(
        call_id="call-1",
        policy=ReplayPolicy.DOMAIN_IDENTITY,
        domain_identity="transfer-42",
    )
    second = acquire(
        call_id="call-2",
        policy=ReplayPolicy.DOMAIN_IDENTITY,
        domain_identity="transfer-42",
    )

    assert first.created
    assert not second.created
    assert first.request.pk == second.request.pk


def test_complete_result_persists_and_reconstructs_call_result(acquire) -> None:
    acquired = acquire()
    completed = complete_with_result(
        acquired.request,
        payload={"status": "Accepted"},
    )

    response = stored_response(completed)

    assert completed.status == InboundProtocolRequest.Status.COMPLETED
    assert completed.completed_at is not None
    assert response == CallResult(
        unique_id="call-1",
        payload={"status": "Accepted"},
    )


def test_stored_result_can_use_retransmitted_call_id(acquire) -> None:
    acquired = acquire(
        policy=ReplayPolicy.DOMAIN_IDENTITY,
        domain_identity="transfer-42",
    )
    completed = complete_with_result(
        acquired.request,
        payload={"status": "Accepted"},
    )

    response = stored_response(completed, call_id="call-2")

    assert response.unique_id == "call-2"


def test_complete_error_persists_and_reconstructs_call_error(acquire) -> None:
    acquired = acquire()
    completed = complete_with_error(
        acquired.request,
        code="FormationViolation",
        description="Invalid payload.",
        details={"field": "vendorId"},
    )

    response = stored_response(completed)

    assert response == CallError(
        unique_id="call-1",
        code="FormationViolation",
        description="Invalid payload.",
        details={"field": "vendorId"},
    )


def test_identical_repeat_completion_is_idempotent(acquire) -> None:
    acquired = acquire()
    first = complete_with_result(
        acquired.request,
        payload={"status": "Accepted"},
    )
    second = complete_with_result(
        acquired.request,
        payload={"status": "Accepted"},
    )

    assert first.pk == second.pk
    assert InboundProtocolRequest.objects.filter(pk=first.pk).count() == 1


def test_conflicting_repeat_completion_is_rejected(acquire) -> None:
    acquired = acquire()
    complete_with_result(
        acquired.request,
        payload={"status": "Accepted"},
    )

    with pytest.raises(
        ValueError,
        match="Inbound request already completed with another response.",
    ):
        complete_with_result(
            acquired.request,
            payload={"status": "Rejected"},
        )


def test_processing_request_has_no_stored_response(acquire) -> None:
    acquired = acquire()

    with pytest.raises(ValueError, match="Inbound request has not completed."):
        stored_response(acquired.request)


@override_settings(OCPP_REPLAY_STALE_SECONDS=60)
def test_old_processing_request_becomes_stale_on_reacquisition(acquire) -> None:
    first = acquire()
    InboundProtocolRequest.objects.filter(pk=first.request.pk).update(
        received_at=timezone.now() - timedelta(minutes=2)
    )

    second = acquire()

    assert not second.created
    assert second.stale
    assert second.request.status == InboundProtocolRequest.Status.STALE
    assert second.request.stale_at is not None

import pytest
from django.db import IntegrityError

from apps.ocpp.models import InboundProtocolRequest
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.replay import (
    ReplayPolicy,
    identity_key,
    replay_context_for_action,
    replay_identity,
)
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


@pytest.fixture
def replay_charger():
    return charger("charger-replay")


@pytest.fixture
def request_factory(replay_charger):
    def create_request(
        *,
        call_id: str = "call-1",
        payload: dict[str, object] | None = None,
        policy: ReplayPolicy = ReplayPolicy.CALL_ID_AND_FINGERPRINT,
        domain_identity: str = "",
    ) -> InboundProtocolRequest:
        selected_payload = payload or {"vendorId": "vendor"}
        identity = replay_identity(
            version=ProtocolVersion.OCPP_16,
            action="DataTransfer",
            call_id=call_id,
            payload=selected_payload,
            policy=policy,
            domain_identity=domain_identity,
        )
        return InboundProtocolRequest.objects.create(
            charger=replay_charger,
            version=ProtocolVersion.OCPP_16.value,
            action="DataTransfer",
            unique_id=call_id,
            fingerprint=identity.fingerprint,
            replay_policy=policy.value,
            domain_identity=domain_identity,
            identity_key=identity_key(identity),
            request_payload=selected_payload,
        )

    return create_request


def test_new_request_defaults_to_processing_without_response(request_factory) -> None:
    request = request_factory()

    assert request.status == InboundProtocolRequest.Status.PROCESSING
    assert request.response_kind == ""
    assert request.response_payload is None
    assert request.completed_at is None
    assert request.request_payload == {"vendorId": "vendor"}


def test_same_logical_identity_is_unique(request_factory) -> None:
    request_factory()

    with pytest.raises(IntegrityError):
        request_factory()


def test_reused_call_id_with_changed_payload_is_distinct(request_factory) -> None:
    first = request_factory(payload={"vendorId": "vendor", "data": "one"})
    second = request_factory(payload={"vendorId": "vendor", "data": "two"})

    assert first.identity_key != second.identity_key
    assert InboundProtocolRequest.objects.filter(unique_id="call-1").count() == 2


def test_domain_identity_can_span_different_call_ids(request_factory) -> None:
    first = request_factory(
        call_id="call-1",
        policy=ReplayPolicy.DOMAIN_IDENTITY,
        domain_identity="transfer-42",
    )
    identity = replay_identity(
        version=ProtocolVersion.OCPP_16,
        action="DataTransfer",
        call_id="call-2",
        payload={"vendorId": "vendor"},
        policy=ReplayPolicy.DOMAIN_IDENTITY,
        domain_identity="transfer-42",
    )

    with pytest.raises(IntegrityError):
        InboundProtocolRequest.objects.create(
            charger=first.charger,
            version=ProtocolVersion.OCPP_16.value,
            action="DataTransfer",
            unique_id="call-2",
            fingerprint=identity.fingerprint,
            replay_policy=ReplayPolicy.DOMAIN_IDENTITY.value,
            domain_identity="transfer-42",
            identity_key=identity_key(identity),
            request_payload={"vendorId": "vendor"},
        )


def test_different_chargers_may_share_same_logical_identity(request_factory) -> None:
    first = request_factory()
    identity = replay_identity(
        version=ProtocolVersion.OCPP_16,
        action="DataTransfer",
        call_id="call-1",
        payload={"vendorId": "vendor"},
    )

    second = InboundProtocolRequest.objects.create(
        charger=charger("charger-other"),
        version=ProtocolVersion.OCPP_16.value,
        action="DataTransfer",
        unique_id="call-1",
        fingerprint=identity.fingerprint,
        replay_policy=ReplayPolicy.CALL_ID_AND_FINGERPRINT.value,
        identity_key=identity_key(identity),
        request_payload={"vendorId": "vendor"},
    )

    assert first.pk != second.pk


def test_sequenced_report_chunk_uses_domain_identity() -> None:
    policy, domain_identity = replay_context_for_action(
        "NotifyReport",
        {"requestId": 42, "seqNo": 3, "tbc": True},
    )

    assert policy == ReplayPolicy.DOMAIN_IDENTITY
    assert domain_identity == "NotifyReport:42:3"


def test_monitoring_report_sequence_is_part_of_identity() -> None:
    first = replay_context_for_action(
        "NotifyMonitoringReport",
        {"requestId": 9, "seqNo": 1},
    )
    second = replay_context_for_action(
        "NotifyMonitoringReport",
        {"requestId": 9, "seqNo": 2},
    )

    assert first[1] != second[1]


def test_unsequenced_charging_profile_report_remains_bounded() -> None:
    policy, domain_identity = replay_context_for_action(
        "ReportChargingProfiles",
        {"requestId": 7, "evseId": 1},
    )

    assert policy == ReplayPolicy.NO_CROSS_CALL_DEDUP
    assert domain_identity == ""

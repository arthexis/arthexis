import json

import pytest

from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.replay import (
    ReplayPolicy,
    canonical_payload,
    replay_context_for_action,
    replay_identity,
    replay_policy_for_action,
    request_fingerprint,
)


def test_dictionary_key_order_does_not_change_canonical_payload() -> None:
    first = {"nested": {"b": 2, "a": 1}, "value": "é"}
    second = {"value": "é", "nested": {"a": 1, "b": 2}}

    assert canonical_payload(first) == canonical_payload(second)


def test_canonical_payload_is_compact_utf8_json() -> None:
    canonical = canonical_payload({"message": "café", "count": 2})

    assert canonical == '{"count":2,"message":"café"}'
    assert json.loads(canonical) == {"message": "café", "count": 2}


def test_non_json_numeric_values_are_rejected() -> None:
    with pytest.raises(ValueError):
        canonical_payload({"value": float("nan")})


def test_mapping_order_does_not_change_fingerprint() -> None:
    first = request_fingerprint(
        version=ProtocolVersion.OCPP_16,
        action="DataTransfer",
        payload={"vendorId": "vendor", "data": {"b": 2, "a": 1}},
    )
    second = request_fingerprint(
        version=ProtocolVersion.OCPP_16,
        action="DataTransfer",
        payload={"data": {"a": 1, "b": 2}, "vendorId": "vendor"},
    )

    assert first == second


def test_payload_change_changes_fingerprint() -> None:
    first = request_fingerprint(
        version=ProtocolVersion.OCPP_16,
        action="DataTransfer",
        payload={"vendorId": "vendor", "data": "one"},
    )
    second = request_fingerprint(
        version=ProtocolVersion.OCPP_16,
        action="DataTransfer",
        payload={"vendorId": "vendor", "data": "two"},
    )

    assert first != second


def test_action_and_version_are_part_of_fingerprint_namespace() -> None:
    payload = {"status": "Accepted"}
    base = request_fingerprint(
        version=ProtocolVersion.OCPP_16,
        action="DataTransfer",
        payload=payload,
    )

    assert base != request_fingerprint(
        version=ProtocolVersion.OCPP_16,
        action="FirmwareStatusNotification",
        payload=payload,
    )
    assert base != request_fingerprint(
        version=ProtocolVersion.OCPP_201,
        action="DataTransfer",
        payload=payload,
    )


def test_default_identity_uses_call_id_and_fingerprint() -> None:
    identity = replay_identity(
        version=ProtocolVersion.OCPP_16,
        action="Heartbeat",
        call_id="call-1",
        payload={},
    )

    assert identity.policy == ReplayPolicy.CALL_ID_AND_FINGERPRINT
    assert identity.logical_key() == (
        ReplayPolicy.CALL_ID_AND_FINGERPRINT.value,
        "call-1",
        identity.fingerprint,
    )


def test_changed_payload_with_reused_call_id_has_distinct_identity() -> None:
    first = replay_identity(
        version=ProtocolVersion.OCPP_16,
        action="DataTransfer",
        call_id="reused",
        payload={"vendorId": "vendor", "data": "one"},
    )
    second = replay_identity(
        version=ProtocolVersion.OCPP_16,
        action="DataTransfer",
        call_id="reused",
        payload={"vendorId": "vendor", "data": "two"},
    )

    assert first.logical_key() != second.logical_key()


def test_identical_payload_with_new_call_id_is_not_cross_call_deduplicated() -> None:
    first = replay_identity(
        version=ProtocolVersion.OCPP_16,
        action="Heartbeat",
        call_id="call-1",
        payload={},
        policy=ReplayPolicy.NO_CROSS_CALL_DEDUP,
    )
    second = replay_identity(
        version=ProtocolVersion.OCPP_16,
        action="Heartbeat",
        call_id="call-2",
        payload={},
        policy=ReplayPolicy.NO_CROSS_CALL_DEDUP,
    )

    assert first.logical_key() != second.logical_key()


def test_domain_identity_requires_explicit_domain_key() -> None:
    with pytest.raises(ValueError, match=r"Domain replay identity is required\."):
        replay_identity(
            version=ProtocolVersion.OCPP_201,
            action="TransactionEvent",
            call_id="call-1",
            payload={"eventType": "Updated"},
            policy=ReplayPolicy.DOMAIN_IDENTITY,
        )


def test_domain_identity_can_correlate_different_call_ids() -> None:
    first = replay_identity(
        version=ProtocolVersion.OCPP_201,
        action="TransactionEvent",
        call_id="call-1",
        payload={"eventType": "Updated", "seqNo": 4},
        policy=ReplayPolicy.DOMAIN_IDENTITY,
        domain_identity="tx-1:4",
    )
    second = replay_identity(
        version=ProtocolVersion.OCPP_201,
        action="TransactionEvent",
        call_id="call-2",
        payload={"seqNo": 4, "eventType": "Updated"},
        policy=ReplayPolicy.DOMAIN_IDENTITY,
        domain_identity="tx-1:4",
    )

    assert first.logical_key() == second.logical_key()


@pytest.mark.parametrize("action", ["Heartbeat", "DataTransfer"])
def test_repeatable_action_uses_bounded_replay_policy(action: str) -> None:
    assert replay_policy_for_action(action) == ReplayPolicy.NO_CROSS_CALL_DEDUP


@pytest.mark.parametrize("action", ["StartTransaction", "StopTransaction", "MeterValues"])
def test_transaction_actions_keep_durable_replay_policy(action: str) -> None:
    assert replay_policy_for_action(action) == ReplayPolicy.CALL_ID_AND_FINGERPRINT


def test_transaction_event_uses_protocol_domain_identity() -> None:
    payload = {
        "eventType": "Updated",
        "seqNo": 4,
        "transactionInfo": {"transactionId": "tx-1"},
    }

    policy, domain_identity = replay_context_for_action("TransactionEvent", payload)

    assert policy == ReplayPolicy.DOMAIN_IDENTITY
    assert domain_identity == "tx-1:4:Updated"
    assert replay_policy_for_action("TransactionEvent") == ReplayPolicy.DOMAIN_IDENTITY


def test_malformed_transaction_event_falls_back_to_call_identity() -> None:
    policy, domain_identity = replay_context_for_action(
        "TransactionEvent",
        {"eventType": "Updated"},
    )

    assert policy == ReplayPolicy.CALL_ID_AND_FINGERPRINT
    assert domain_identity == ""

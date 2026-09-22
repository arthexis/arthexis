import json
from unittest import TestCase

from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.replay import (
    ReplayPolicy,
    canonical_payload,
    replay_identity,
    request_fingerprint,
)


class CanonicalPayloadTests(TestCase):
    def test_dictionary_key_order_does_not_change_canonical_payload(self) -> None:
        first = {"nested": {"b": 2, "a": 1}, "value": "é"}
        second = {"value": "é", "nested": {"a": 1, "b": 2}}

        self.assertEqual(canonical_payload(first), canonical_payload(second))

    def test_canonical_payload_is_compact_utf8_json(self) -> None:
        canonical = canonical_payload({"message": "café", "count": 2})

        self.assertEqual(canonical, '{"count":2,"message":"café"}')
        self.assertEqual(json.loads(canonical), {"message": "café", "count": 2})

    def test_non_json_numeric_values_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            canonical_payload({"value": float("nan")})


class RequestFingerprintTests(TestCase):
    def test_mapping_order_does_not_change_fingerprint(self) -> None:
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

        self.assertEqual(first, second)

    def test_payload_change_changes_fingerprint(self) -> None:
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

        self.assertNotEqual(first, second)

    def test_action_and_version_are_part_of_fingerprint_namespace(self) -> None:
        payload = {"status": "Accepted"}
        base = request_fingerprint(
            version=ProtocolVersion.OCPP_16,
            action="DataTransfer",
            payload=payload,
        )

        self.assertNotEqual(
            base,
            request_fingerprint(
                version=ProtocolVersion.OCPP_16,
                action="FirmwareStatusNotification",
                payload=payload,
            ),
        )
        self.assertNotEqual(
            base,
            request_fingerprint(
                version=ProtocolVersion.OCPP_201,
                action="DataTransfer",
                payload=payload,
            ),
        )


class ReplayIdentityTests(TestCase):
    def test_default_identity_uses_call_id_and_fingerprint(self) -> None:
        identity = replay_identity(
            version=ProtocolVersion.OCPP_16,
            action="Heartbeat",
            call_id="call-1",
            payload={},
        )

        self.assertEqual(identity.policy, ReplayPolicy.CALL_ID_AND_FINGERPRINT)
        self.assertEqual(
            identity.logical_key(),
            (
                ReplayPolicy.CALL_ID_AND_FINGERPRINT.value,
                "call-1",
                identity.fingerprint,
            ),
        )

    def test_changed_payload_with_reused_call_id_has_distinct_identity(self) -> None:
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

        self.assertNotEqual(first.logical_key(), second.logical_key())

    def test_identical_payload_with_new_call_id_is_not_cross_call_deduplicated(self) -> None:
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

        self.assertNotEqual(first.logical_key(), second.logical_key())

    def test_domain_identity_requires_explicit_domain_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "Domain replay identity is required\\."):
            replay_identity(
                version=ProtocolVersion.OCPP_201,
                action="TransactionEvent",
                call_id="call-1",
                payload={"eventType": "Updated"},
                policy=ReplayPolicy.DOMAIN_IDENTITY,
            )

    def test_domain_identity_can_correlate_different_call_ids(self) -> None:
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

        self.assertEqual(first.logical_key(), second.logical_key())

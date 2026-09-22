from django.db import IntegrityError
from django.test import TestCase

from apps.ocpp.models import InboundProtocolRequest
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.replay import (
    ReplayPolicy,
    identity_key,
    replay_context_for_action,
    replay_identity,
)
from tests.apps.ocpp.builders import charger


class InboundProtocolRequestModelTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("charger-replay")

    def create_request(
        self,
        *,
        call_id: str = "call-1",
        payload: dict[str, object] | None = None,
        policy: ReplayPolicy = ReplayPolicy.CALL_ID_AND_FINGERPRINT,
        domain_identity: str = "",
    ) -> InboundProtocolRequest:
        selected = self.charger
        payload = payload or {"vendorId": "vendor"}
        identity = replay_identity(
            version=ProtocolVersion.OCPP_16,
            action="DataTransfer",
            call_id=call_id,
            payload=payload,
            policy=policy,
            domain_identity=domain_identity,
        )
        return InboundProtocolRequest.objects.create(
            charger=selected,
            version=ProtocolVersion.OCPP_16.value,
            action="DataTransfer",
            unique_id=call_id,
            fingerprint=identity.fingerprint,
            replay_policy=policy.value,
            domain_identity=domain_identity,
            identity_key=identity_key(identity),
            request_payload=payload,
        )

    def test_new_request_defaults_to_processing_without_response(self) -> None:
        request = self.create_request()

        self.assertEqual(request.status, InboundProtocolRequest.Status.PROCESSING)
        self.assertEqual(request.response_kind, "")
        self.assertIsNone(request.response_payload)
        self.assertIsNone(request.completed_at)
        self.assertEqual(request.request_payload, {"vendorId": "vendor"})

    def test_same_logical_identity_is_unique(self) -> None:
        self.create_request()

        with self.assertRaises(IntegrityError):
            self.create_request()

    def test_reused_call_id_with_changed_payload_is_distinct(self) -> None:
        first = self.create_request(payload={"vendorId": "vendor", "data": "one"})
        second = self.create_request(payload={"vendorId": "vendor", "data": "two"})

        self.assertNotEqual(first.identity_key, second.identity_key)
        self.assertEqual(
            InboundProtocolRequest.objects.filter(unique_id="call-1").count(),
            2,
        )

    def test_domain_identity_can_span_different_call_ids(self) -> None:
        first = self.create_request(
            call_id="call-1",
            payload={"vendorId": "vendor"},
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

        with self.assertRaises(IntegrityError):
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

    def test_different_chargers_may_share_same_logical_identity(self) -> None:
        first = self.create_request()
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

        self.assertNotEqual(first.pk, second.pk)



class ReportReplayIdentityTests(TestCase):
    def test_sequenced_report_chunk_uses_domain_identity(self) -> None:
        policy, domain_identity = replay_context_for_action(
            "NotifyReport",
            {"requestId": 42, "seqNo": 3, "tbc": True},
        )

        self.assertEqual(policy, ReplayPolicy.DOMAIN_IDENTITY)
        self.assertEqual(domain_identity, "NotifyReport:42:3")

    def test_monitoring_report_sequence_is_part_of_identity(self) -> None:
        first = replay_context_for_action(
            "NotifyMonitoringReport",
            {"requestId": 9, "seqNo": 1},
        )
        second = replay_context_for_action(
            "NotifyMonitoringReport",
            {"requestId": 9, "seqNo": 2},
        )

        self.assertNotEqual(first[1], second[1])

    def test_unsequenced_charging_profile_report_remains_bounded(self) -> None:
        policy, domain_identity = replay_context_for_action(
            "ReportChargingProfiles",
            {"requestId": 7, "evseId": 1},
        )

        self.assertEqual(policy, ReplayPolicy.NO_CROSS_CALL_DEDUP)
        self.assertEqual(domain_identity, "")

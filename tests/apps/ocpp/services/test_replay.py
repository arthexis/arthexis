from django.test import TestCase

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


class ReplayPersistenceTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("charger-replay-service")

    def acquire(
        self,
        *,
        call_id: str = "call-1",
        payload: dict[str, object] | None = None,
        policy: ReplayPolicy = ReplayPolicy.CALL_ID_AND_FINGERPRINT,
        domain_identity: str = "",
    ):
        return acquire_inbound_request(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            action="DataTransfer",
            call_id=call_id,
            payload=payload or {"vendorId": "vendor"},
            policy=policy,
            domain_identity=domain_identity,
        )

    def test_first_acquisition_creates_processing_request(self) -> None:
        acquired = self.acquire()

        self.assertTrue(acquired.created)
        self.assertFalse(acquired.completed)
        self.assertEqual(
            acquired.request.status,
            InboundProtocolRequest.Status.PROCESSING,
        )

    def test_repeat_acquisition_recovers_same_request(self) -> None:
        first = self.acquire()
        second = self.acquire()

        self.assertFalse(second.created)
        self.assertEqual(second.request.pk, first.request.pk)

    def test_reused_call_id_with_changed_payload_creates_distinct_request(self) -> None:
        first = self.acquire(payload={"vendorId": "vendor", "data": "one"})
        second = self.acquire(payload={"vendorId": "vendor", "data": "two"})

        self.assertTrue(first.created)
        self.assertTrue(second.created)
        self.assertNotEqual(first.request.pk, second.request.pk)

    def test_domain_identity_recovers_request_across_call_ids(self) -> None:
        first = self.acquire(
            call_id="call-1",
            policy=ReplayPolicy.DOMAIN_IDENTITY,
            domain_identity="transfer-42",
        )
        second = self.acquire(
            call_id="call-2",
            policy=ReplayPolicy.DOMAIN_IDENTITY,
            domain_identity="transfer-42",
        )

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.request.pk, second.request.pk)

    def test_complete_result_persists_and_reconstructs_call_result(self) -> None:
        acquired = self.acquire()
        completed = complete_with_result(
            acquired.request,
            payload={"status": "Accepted"},
        )

        response = stored_response(completed)

        self.assertEqual(completed.status, InboundProtocolRequest.Status.COMPLETED)
        self.assertIsNotNone(completed.completed_at)
        self.assertEqual(
            response,
            CallResult(unique_id="call-1", payload={"status": "Accepted"}),
        )

    def test_stored_result_can_use_retransmitted_call_id(self) -> None:
        acquired = self.acquire(
            policy=ReplayPolicy.DOMAIN_IDENTITY,
            domain_identity="transfer-42",
        )
        completed = complete_with_result(
            acquired.request,
            payload={"status": "Accepted"},
        )

        response = stored_response(completed, call_id="call-2")

        self.assertEqual(response.unique_id, "call-2")

    def test_complete_error_persists_and_reconstructs_call_error(self) -> None:
        acquired = self.acquire()
        completed = complete_with_error(
            acquired.request,
            code="FormationViolation",
            description="Invalid payload.",
            details={"field": "vendorId"},
        )

        response = stored_response(completed)

        self.assertEqual(
            response,
            CallError(
                unique_id="call-1",
                code="FormationViolation",
                description="Invalid payload.",
                details={"field": "vendorId"},
            ),
        )

    def test_identical_repeat_completion_is_idempotent(self) -> None:
        acquired = self.acquire()
        first = complete_with_result(
            acquired.request,
            payload={"status": "Accepted"},
        )
        second = complete_with_result(
            acquired.request,
            payload={"status": "Accepted"},
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            InboundProtocolRequest.objects.filter(pk=first.pk).count(),
            1,
        )

    def test_conflicting_repeat_completion_is_rejected(self) -> None:
        acquired = self.acquire()
        complete_with_result(
            acquired.request,
            payload={"status": "Accepted"},
        )

        with self.assertRaisesMessage(
            ValueError,
            "Inbound request already completed with another response.",
        ):
            complete_with_result(
                acquired.request,
                payload={"status": "Rejected"},
            )

    def test_processing_request_has_no_stored_response(self) -> None:
        acquired = self.acquire()

        with self.assertRaisesMessage(ValueError, "Inbound request has not completed."):
            stored_response(acquired.request)

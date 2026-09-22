from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.cards.models import AuthorizationAttempt
from apps.ocpp.models import InboundProtocolRequest, OcppTransaction
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallResult
from apps.ocpp.protocol.v16.inbound import InboundActions
from apps.ocpp.protocol.v201.inbound import InboundActions as Inbound201Actions
from apps.ocpp.transport.dispatch import FrameDispatcher
from tests.apps.ocpp.builders import charger


class V16StartTransactionRecoveryTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("start-recovery")

    def _dispatcher(self) -> FrameDispatcher:
        return FrameDispatcher(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            pending_calls=PendingCalls(),
            handler_resolver=InboundActions(self.charger).resolve,
        )

    def _frame(self) -> Call:
        return Call(
            unique_id="start-1",
            action="StartTransaction",
            payload={
                "connectorId": 1,
                "idTag": "guest-card",
                "meterStart": 100,
                "timestamp": "2026-09-22T10:00:00Z",
            },
        )

    def test_lost_response_replay_returns_same_transaction_without_side_effects(
        self,
    ) -> None:
        first = async_to_sync(self._dispatcher().dispatch)(self._frame())

        self.assertIsInstance(first, CallResult)
        transaction_id = first.payload["transactionId"]
        self.assertEqual(OcppTransaction.objects.count(), 1)
        self.assertEqual(AuthorizationAttempt.objects.count(), 1)

        replayed = async_to_sync(self._dispatcher().dispatch)(self._frame())

        self.assertEqual(replayed, first)
        self.assertEqual(replayed.payload["transactionId"], transaction_id)
        self.assertEqual(OcppTransaction.objects.count(), 1)
        self.assertEqual(AuthorizationAttempt.objects.count(), 1)

    def test_replay_completion_failure_rolls_back_transaction_and_authorization(
        self,
    ) -> None:
        with patch(
            "apps.ocpp.services.transactions.complete_with_result",
            side_effect=RuntimeError("database completion failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "database completion failed"):
                async_to_sync(self._dispatcher().dispatch)(self._frame())

        self.assertFalse(OcppTransaction.objects.exists())
        self.assertFalse(AuthorizationAttempt.objects.exists())
        request = InboundProtocolRequest.objects.get(
            charger=self.charger,
            action="StartTransaction",
            unique_id="start-1",
        )
        self.assertEqual(request.status, InboundProtocolRequest.Status.PROCESSING)
        self.assertIsNone(request.completed_at)



class V201TransactionEventRecoveryTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("transaction-event-recovery")

    def _dispatcher(self) -> FrameDispatcher:
        return FrameDispatcher(
            charger=self.charger,
            version=ProtocolVersion.OCPP_201,
            pending_calls=PendingCalls(),
            handler_resolver=Inbound201Actions(self.charger).resolve,
        )

    @staticmethod
    def _payload(
        *,
        event_type: str = "Started",
        seq_no: int = 1,
        offline: bool = False,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "eventType": event_type,
            "timestamp": "2026-09-22T10:00:00Z",
            "triggerReason": "Authorized",
            "seqNo": seq_no,
            "transactionInfo": {"transactionId": "remote-201"},
            "idToken": {"idToken": "guest-card", "type": "Central"},
            "evse": {"id": 1, "connectorId": 1},
        }
        if offline:
            payload["offline"] = True
        return payload

    def test_same_domain_event_with_new_call_id_replays_without_side_effects(
        self,
    ) -> None:
        first = async_to_sync(self._dispatcher().dispatch)(
            Call(
                unique_id="event-call-1",
                action="TransactionEvent",
                payload=self._payload(),
            )
        )

        self.assertIsInstance(first, CallResult)
        self.assertEqual(OcppTransaction.objects.count(), 1)
        self.assertEqual(AuthorizationAttempt.objects.count(), 1)

        replayed = async_to_sync(self._dispatcher().dispatch)(
            Call(
                unique_id="event-call-2",
                action="TransactionEvent",
                payload=self._payload(),
            )
        )

        self.assertIsInstance(replayed, CallResult)
        self.assertEqual(replayed.payload, first.payload)
        self.assertEqual(replayed.unique_id, "event-call-2")
        self.assertEqual(OcppTransaction.objects.count(), 1)
        self.assertEqual(AuthorizationAttempt.objects.count(), 1)
        self.assertEqual(
            InboundProtocolRequest.objects.filter(
                charger=self.charger,
                action="TransactionEvent",
            ).count(),
            1,
        )

    def test_next_sequence_is_distinct_without_reauthorizing_known_transaction(
        self,
    ) -> None:
        async_to_sync(self._dispatcher().dispatch)(
            Call(
                unique_id="event-start",
                action="TransactionEvent",
                payload=self._payload(seq_no=1),
            )
        )
        async_to_sync(self._dispatcher().dispatch)(
            Call(
                unique_id="event-update",
                action="TransactionEvent",
                payload=self._payload(event_type="Updated", seq_no=2),
            )
        )

        self.assertEqual(OcppTransaction.objects.count(), 1)
        self.assertEqual(AuthorizationAttempt.objects.count(), 1)
        self.assertEqual(
            InboundProtocolRequest.objects.filter(
                charger=self.charger,
                action="TransactionEvent",
            ).count(),
            2,
        )

    def test_offline_historical_start_persists_without_live_authorization(self) -> None:
        self.charger.authorization_mode = self.charger.AuthorizationMode.RESTRICTED
        self.charger.save(update_fields=("authorization_mode",))

        response = async_to_sync(self._dispatcher().dispatch)(
            Call(
                unique_id="offline-start",
                action="TransactionEvent",
                payload=self._payload(offline=True),
            )
        )

        self.assertIsInstance(response, CallResult)
        self.assertEqual(response.payload, {"idTokenInfo": {"status": "Accepted"}})
        self.assertEqual(OcppTransaction.objects.count(), 1)
        self.assertFalse(AuthorizationAttempt.objects.exists())

    def test_completion_failure_rolls_back_transaction_event_and_authorization(
        self,
    ) -> None:
        with patch(
            "apps.ocpp.services.transactions.complete_with_result",
            side_effect=RuntimeError("database completion failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "database completion failed"):
                async_to_sync(self._dispatcher().dispatch)(
                    Call(
                        unique_id="event-failure",
                        action="TransactionEvent",
                        payload=self._payload(),
                    )
                )

        self.assertFalse(OcppTransaction.objects.exists())
        self.assertFalse(AuthorizationAttempt.objects.exists())
        request = InboundProtocolRequest.objects.get(
            charger=self.charger,
            action="TransactionEvent",
            unique_id="event-failure",
        )
        self.assertEqual(request.status, InboundProtocolRequest.Status.PROCESSING)

from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.cards.models import AuthorizationAttempt
from apps.ocpp.models import InboundProtocolRequest, OcppTransaction
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallResult
from apps.ocpp.protocol.v16.inbound import InboundActions
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

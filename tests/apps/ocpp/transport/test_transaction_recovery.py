from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.cards.models import AuthorizationAttempt
from apps.ocpp.models import InboundProtocolRequest, MeterValue, OcppTransaction
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



class MeterValueRecoveryTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("meter-recovery")
        self.transaction = OcppTransaction.objects.create(
            charger=self.charger,
            remote_id="remote-meter",
            started_at="2026-09-22T10:00:00Z",
            last_activity_at="2026-09-22T10:00:00Z",
        )

    def _v16_dispatcher(self) -> FrameDispatcher:
        return FrameDispatcher(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            pending_calls=PendingCalls(),
            handler_resolver=InboundActions(self.charger).resolve,
        )

    def _v16_payload(self) -> dict[str, object]:
        return {
            "transactionId": self.transaction.pk,
            "meterValue": [
                {
                    "timestamp": "2026-09-22T10:05:00Z",
                    "sampledValue": [
                        {
                            "value": "125.0",
                            "measurand": "Energy.Active.Import.Register",
                            "unit": "Wh",
                        }
                    ],
                }
            ],
        }

    def test_same_sample_with_different_call_id_is_not_duplicated(self) -> None:
        first = async_to_sync(self._v16_dispatcher().dispatch)(
            Call(
                unique_id="meter-call-1",
                action="MeterValues",
                payload=self._v16_payload(),
            )
        )
        second = async_to_sync(self._v16_dispatcher().dispatch)(
            Call(
                unique_id="meter-call-2",
                action="MeterValues",
                payload=self._v16_payload(),
            )
        )

        self.assertIsInstance(first, CallResult)
        self.assertIsInstance(second, CallResult)
        self.assertEqual(MeterValue.objects.count(), 1)
        sample = MeterValue.objects.get()
        self.assertTrue(sample.source_fingerprint)

    def test_meter_completion_failure_rolls_back_samples_and_activity(self) -> None:
        original_activity = self.transaction.last_activity_at

        with patch(
            "apps.ocpp.services.transactions.complete_with_result",
            side_effect=RuntimeError("database completion failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "database completion failed"):
                async_to_sync(self._v16_dispatcher().dispatch)(
                    Call(
                        unique_id="meter-failure",
                        action="MeterValues",
                        payload=self._v16_payload(),
                    )
                )

        self.assertFalse(MeterValue.objects.exists())
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.last_activity_at, original_activity)
        request = InboundProtocolRequest.objects.get(
            charger=self.charger,
            action="MeterValues",
            unique_id="meter-failure",
        )
        self.assertEqual(request.status, InboundProtocolRequest.Status.PROCESSING)

    def test_stale_meter_request_can_be_reopened_and_completed(self) -> None:
        request = InboundProtocolRequest.objects.create(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16.value,
            action="MeterValues",
            unique_id="meter-stale",
            fingerprint="f" * 64,
            identity_key="i" * 64,
            request_payload=self._v16_payload(),
        )
        InboundProtocolRequest.objects.filter(pk=request.pk).update(
            received_at="2026-09-22T00:00:00Z",
        )

        response = async_to_sync(self._v16_dispatcher().dispatch)(
            Call(
                unique_id="meter-stale",
                action="MeterValues",
                payload=self._v16_payload(),
            )
        )

        self.assertIsInstance(response, CallResult)
        self.assertEqual(MeterValue.objects.count(), 1)
        request.refresh_from_db()
        self.assertEqual(request.status, InboundProtocolRequest.Status.PROCESSING)

from datetime import datetime, timezone
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.cards.models import AuthorizationAttempt
from apps.ocpp.models import InboundProtocolRequest, MeterValue, OcppTransaction
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallError, CallResult
from apps.ocpp.protocol.replay import replay_context_for_action
from apps.ocpp.protocol.v16.inbound import InboundActions
from apps.ocpp.protocol.v201.inbound import InboundActions as Inbound201Actions
from apps.ocpp.services.replay import acquire_inbound_request
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


    def test_pre_cutover_start_is_accepted_without_live_authorization(self) -> None:
        self.charger.authorization_mode = self.charger.AuthorizationMode.RESTRICTED
        self.charger.authority_cutover_at = datetime(
            2026, 9, 22, 12, tzinfo=timezone.utc
        )
        self.charger.save(
            update_fields=("authorization_mode", "authority_cutover_at")
        )

        response = async_to_sync(self._dispatcher().dispatch)(
            Call(
                unique_id="historical-start",
                action="StartTransaction",
                payload={
                    "connectorId": 1,
                    "idTag": "expired-three-years-ago",
                    "meterStart": 100,
                    "timestamp": "2023-04-11T10:00:00Z",
                },
            )
        )

        self.assertEqual(
            response.payload["idTagInfo"],
            {"status": "Accepted"},
        )
        selected = OcppTransaction.objects.get(
            pk=response.payload["transactionId"]
        )
        self.assertTrue(selected.historical)
        self.assertEqual(
            selected.started_at,
            datetime(2023, 4, 11, 10, tzinfo=timezone.utc),
        )
        self.assertEqual(selected.id_tag, "expired-three-years-ago")
        self.assertIsNone(selected.account)
        self.assertFalse(AuthorizationAttempt.objects.exists())

    def test_start_at_cutover_still_uses_live_authorization_policy(self) -> None:
        self.charger.authorization_mode = self.charger.AuthorizationMode.RESTRICTED
        self.charger.authority_cutover_at = datetime(
            2026, 9, 22, 12, tzinfo=timezone.utc
        )
        self.charger.save(
            update_fields=("authorization_mode", "authority_cutover_at")
        )

        response = async_to_sync(self._dispatcher().dispatch)(
            Call(
                unique_id="cutover-start",
                action="StartTransaction",
                payload={
                    "connectorId": 1,
                    "idTag": "unknown-live-card",
                    "meterStart": 100,
                    "timestamp": "2026-09-22T12:00:00Z",
                },
            )
        )

        self.assertEqual(
            response.payload,
            {"idTagInfo": {"status": "Invalid"}},
        )
        self.assertFalse(OcppTransaction.objects.exists())
        self.assertEqual(AuthorizationAttempt.objects.count(), 1)

    def test_historical_start_replay_does_not_duplicate_or_authorize(self) -> None:
        self.charger.authorization_mode = self.charger.AuthorizationMode.RESTRICTED
        self.charger.authority_cutover_at = datetime(
            2026, 9, 22, 12, tzinfo=timezone.utc
        )
        self.charger.save(
            update_fields=("authorization_mode", "authority_cutover_at")
        )
        frame = Call(
            unique_id="historical-replay",
            action="StartTransaction",
            payload={
                "connectorId": 1,
                "idTag": "old-card",
                "meterStart": 10,
                "timestamp": "2023-01-01T00:00:00Z",
            },
        )

        first = async_to_sync(self._dispatcher().dispatch)(frame)
        replayed = async_to_sync(self._dispatcher().dispatch)(frame)

        self.assertEqual(replayed, first)
        self.assertEqual(OcppTransaction.objects.count(), 1)
        self.assertTrue(OcppTransaction.objects.get().historical)
        self.assertFalse(AuthorizationAttempt.objects.exists())

    def test_historical_start_completion_failure_rolls_back_transaction(self) -> None:
        self.charger.authority_cutover_at = datetime(
            2026, 9, 22, 12, tzinfo=timezone.utc
        )
        self.charger.save(update_fields=("authority_cutover_at",))
        frame = Call(
            unique_id="historical-crash",
            action="StartTransaction",
            payload={
                "connectorId": 1,
                "idTag": "old-card",
                "meterStart": 10,
                "timestamp": "2023-01-01T00:00:00Z",
            },
        )

        with patch(
            "apps.ocpp.services.transactions.complete_with_result",
            side_effect=RuntimeError("crash before durable response"),
        ):
            with self.assertRaisesRegex(RuntimeError, "crash before durable response"):
                async_to_sync(self._dispatcher().dispatch)(frame)

        self.assertFalse(OcppTransaction.objects.exists())
        self.assertFalse(AuthorizationAttempt.objects.exists())

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
        started_at = datetime(2026, 9, 22, 10, tzinfo=timezone.utc)
        self.transaction = OcppTransaction.objects.create(
            charger=self.charger,
            remote_id="remote-meter",
            started_at=started_at,
            last_activity_at=started_at,
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
            "meterValue": self._meter_values(),
        }

    def _v201_dispatcher(self) -> FrameDispatcher:
        return FrameDispatcher(
            charger=self.charger,
            version=ProtocolVersion.OCPP_201,
            pending_calls=PendingCalls(),
            handler_resolver=Inbound201Actions(self.charger).resolve,
        )

    def _v201_payload(self) -> dict[str, object]:
        return {
            "evseId": 1,
            "meterValue": self._meter_values(),
            "transactionInfo": {"transactionId": self.transaction.remote_id},
        }

    @staticmethod
    def _meter_values() -> list[dict[str, object]]:
        return [
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
        ]

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

    def test_v201_same_sample_with_different_call_id_is_not_duplicated(self) -> None:
        first = async_to_sync(self._v201_dispatcher().dispatch)(
            Call(
                unique_id="meter-201-1",
                action="MeterValues",
                payload=self._v201_payload(),
            )
        )
        second = async_to_sync(self._v201_dispatcher().dispatch)(
            Call(
                unique_id="meter-201-2",
                action="MeterValues",
                payload=self._v201_payload(),
            )
        )

        self.assertIsInstance(first, CallResult)
        self.assertIsInstance(second, CallResult)
        self.assertEqual(MeterValue.objects.count(), 1)

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
        payload = self._v16_payload()
        policy, domain_identity = replay_context_for_action("MeterValues", payload)
        acquired = acquire_inbound_request(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            action="MeterValues",
            call_id="meter-stale",
            payload=payload,
            policy=policy,
            domain_identity=domain_identity,
        )
        InboundProtocolRequest.objects.filter(pk=acquired.request.pk).update(
            received_at=datetime(2026, 9, 22, 0, tzinfo=timezone.utc),
        )

        response = async_to_sync(self._v16_dispatcher().dispatch)(
            Call(
                unique_id="meter-stale",
                action="MeterValues",
                payload=payload,
            )
        )

        self.assertIsInstance(response, CallResult)
        self.assertEqual(MeterValue.objects.count(), 1)
        acquired.request.refresh_from_db()
        self.assertEqual(
            acquired.request.status,
            InboundProtocolRequest.Status.COMPLETED,
        )
        self.assertIsNone(acquired.request.stale_at)



class HistoricalV16ContinuationTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger(
            "historical-continuation",
            authority_cutover_at=datetime(2026, 9, 22, 12, tzinfo=timezone.utc),
        )
        self.dispatcher = FrameDispatcher(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            pending_calls=PendingCalls(),
            handler_resolver=InboundActions(self.charger).resolve,
        )

    def _start(self, *, call_id: str = "historical-start") -> CallResult:
        response = async_to_sync(self.dispatcher.dispatch)(
            Call(
                unique_id=call_id,
                action="StartTransaction",
                payload={
                    "connectorId": 1,
                    "idTag": "legacy-card",
                    "meterStart": 100,
                    "timestamp": "2023-04-11T10:00:00Z",
                },
            )
        )
        self.assertIsInstance(response, CallResult)
        return response

    def test_historical_meter_and_stop_preserve_source_timestamps(self) -> None:
        started = self._start()
        transaction_id = started.payload["transactionId"]

        metered = async_to_sync(self.dispatcher.dispatch)(
            Call(
                unique_id="historical-meter",
                action="MeterValues",
                payload={
                    "transactionId": transaction_id,
                    "meterValue": [
                        {
                            "timestamp": "2023-04-11T10:05:00Z",
                            "sampledValue": [
                                {
                                    "value": "125",
                                    "measurand": "Energy.Active.Import.Register",
                                    "unit": "Wh",
                                }
                            ],
                        }
                    ],
                },
            )
        )
        self.assertEqual(metered, CallResult(unique_id="historical-meter", payload={}))

        selected = OcppTransaction.objects.get(pk=transaction_id)
        self.assertTrue(selected.historical)
        self.assertEqual(
            selected.last_activity_at,
            datetime(2023, 4, 11, 10, 5, tzinfo=timezone.utc),
        )
        self.assertEqual(
            MeterValue.objects.get(transaction=selected).sampled_at,
            datetime(2023, 4, 11, 10, 5, tzinfo=timezone.utc),
        )
        self.assertFalse(OcppTransaction.objects.active().filter(pk=selected.pk).exists())
        self.assertFalse(OcppTransaction.objects.unresolved().filter(pk=selected.pk).exists())

        stopped = async_to_sync(self.dispatcher.dispatch)(
            Call(
                unique_id="historical-stop",
                action="StopTransaction",
                payload={
                    "transactionId": transaction_id,
                    "meterStop": 150,
                    "timestamp": "2023-04-11T10:10:00Z",
                },
            )
        )
        self.assertEqual(
            stopped,
            CallResult(
                unique_id="historical-stop",
                payload={"idTagInfo": {"status": "Accepted"}},
            ),
        )

        selected.refresh_from_db()
        self.assertTrue(selected.historical)
        self.assertEqual(
            selected.stopped_at,
            datetime(2023, 4, 11, 10, 10, tzinfo=timezone.utc),
        )
        self.assertEqual(
            selected.last_activity_at,
            datetime(2023, 4, 11, 10, 10, tzinfo=timezone.utc),
        )
        self.assertEqual(
            selected.recovery_state,
            OcppTransaction.RecoveryState.COMPLETED,
        )
        self.assertEqual(str(selected.energy_kwh), "0.0500")

    def test_historical_transaction_provenance_is_inherited_by_later_meter_evidence(
        self,
    ) -> None:
        started = self._start()
        transaction_id = started.payload["transactionId"]

        response = async_to_sync(self.dispatcher.dispatch)(
            Call(
                unique_id="clock-drift-meter",
                action="MeterValues",
                payload={
                    "transactionId": transaction_id,
                    "meterValue": [
                        {
                            "timestamp": "2026-09-23T12:00:00Z",
                            "sampledValue": [{"value": "125"}],
                        }
                    ],
                },
            )
        )

        self.assertEqual(response, CallResult(unique_id="clock-drift-meter", payload={}))
        selected = OcppTransaction.objects.get(pk=transaction_id)
        self.assertTrue(selected.historical)
        self.assertFalse(OcppTransaction.objects.active().filter(pk=selected.pk).exists())
        self.assertFalse(OcppTransaction.objects.open().filter(pk=selected.pk).exists())

    def test_exact_historical_meter_and_stop_replay_do_not_duplicate_state(self) -> None:
        started = self._start()
        transaction_id = started.payload["transactionId"]
        meter = Call(
            unique_id="historical-meter-replay",
            action="MeterValues",
            payload={
                "transactionId": transaction_id,
                "meterValue": [
                    {
                        "timestamp": "2023-04-11T10:05:00Z",
                        "sampledValue": [{"value": "125"}],
                    }
                ],
            },
        )
        stop = Call(
            unique_id="historical-stop-replay",
            action="StopTransaction",
            payload={
                "transactionId": transaction_id,
                "meterStop": 150,
                "timestamp": "2023-04-11T10:10:00Z",
            },
        )

        first_meter = async_to_sync(self.dispatcher.dispatch)(meter)
        replay_meter = async_to_sync(self.dispatcher.dispatch)(meter)
        first_stop = async_to_sync(self.dispatcher.dispatch)(stop)
        replay_stop = async_to_sync(self.dispatcher.dispatch)(stop)

        self.assertEqual(replay_meter, first_meter)
        self.assertEqual(replay_stop, first_stop)
        self.assertEqual(MeterValue.objects.filter(transaction_id=transaction_id).count(), 1)
        self.assertEqual(OcppTransaction.objects.filter(pk=transaction_id).count(), 1)
        self.assertTrue(OcppTransaction.objects.get(pk=transaction_id).historical)

    def test_historical_meter_completion_failure_rolls_back_samples_and_activity(self) -> None:
        started = self._start()
        selected = OcppTransaction.objects.get(pk=started.payload["transactionId"])
        original_activity = selected.last_activity_at

        with patch(
            "apps.ocpp.services.transactions.complete_with_result",
            side_effect=RuntimeError("historical meter replay completion failed"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "historical meter replay completion failed",
            ):
                async_to_sync(self.dispatcher.dispatch)(
                    Call(
                        unique_id="historical-meter-failure",
                        action="MeterValues",
                        payload={
                            "transactionId": selected.pk,
                            "meterValue": [
                                {
                                    "timestamp": "2023-04-11T10:05:00Z",
                                    "sampledValue": [{"value": "125"}],
                                }
                            ],
                        },
                    )
                )

        self.assertFalse(MeterValue.objects.filter(transaction=selected).exists())
        selected.refresh_from_db()
        self.assertTrue(selected.historical)
        self.assertEqual(selected.last_activity_at, original_activity)

    def test_historical_stop_completion_failure_rolls_back_closeout(self) -> None:
        started = self._start()
        selected = OcppTransaction.objects.get(pk=started.payload["transactionId"])

        with patch(
            "apps.ocpp.services.transactions.complete_with_result",
            side_effect=RuntimeError("historical stop replay completion failed"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "historical stop replay completion failed",
            ):
                async_to_sync(self.dispatcher.dispatch)(
                    Call(
                        unique_id="historical-stop-failure",
                        action="StopTransaction",
                        payload={
                            "transactionId": selected.pk,
                            "meterStop": 150,
                            "timestamp": "2023-04-11T10:10:00Z",
                        },
                    )
                )

        selected.refresh_from_db()
        self.assertTrue(selected.historical)
        self.assertIsNone(selected.stopped_at)
        self.assertEqual(
            selected.recovery_state,
            OcppTransaction.RecoveryState.ACTIVE,
        )


class ReconnectReconciliationTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("reconnect-recovery")

    def test_v16_available_status_marks_open_transaction_unresolved(self) -> None:
        started = async_to_sync(
            FrameDispatcher(
                charger=self.charger,
                version=ProtocolVersion.OCPP_16,
                pending_calls=PendingCalls(),
                handler_resolver=InboundActions(self.charger).resolve,
            ).dispatch
        )(
            Call(
                unique_id="start-reconcile",
                action="StartTransaction",
                payload={
                    "connectorId": 1,
                    "idTag": "guest-card",
                    "meterStart": 100,
                    "timestamp": "2026-09-22T10:00:00Z",
                },
            )
        )
        self.assertIsInstance(started, CallResult)
        transaction_id = started.payload["transactionId"]

        response = async_to_sync(
            FrameDispatcher(
                charger=self.charger,
                version=ProtocolVersion.OCPP_16,
                pending_calls=PendingCalls(),
                handler_resolver=InboundActions(self.charger).resolve,
            ).dispatch
        )(
            Call(
                unique_id="status-reconcile",
                action="StatusNotification",
                payload={
                    "connectorId": 1,
                    "status": "Available",
                    "timestamp": "2026-09-22T10:20:00Z",
                },
            )
        )

        self.assertIsInstance(response, CallResult)
        selected = OcppTransaction.objects.get(pk=transaction_id)
        self.assertEqual(
            selected.recovery_state,
            OcppTransaction.RecoveryState.UNRESOLVED,
        )
        self.assertIsNone(selected.stopped_at)

    def test_v201_available_then_newer_transaction_evidence_reactivates(self) -> None:
        dispatcher = FrameDispatcher(
            charger=self.charger,
            version=ProtocolVersion.OCPP_201,
            pending_calls=PendingCalls(),
            handler_resolver=Inbound201Actions(self.charger).resolve,
        )
        start_payload = {
            "eventType": "Started",
            "timestamp": "2026-09-22T10:00:00Z",
            "triggerReason": "Authorized",
            "seqNo": 1,
            "transactionInfo": {"transactionId": "reconnect-201"},
            "idToken": {"idToken": "guest-card", "type": "Central"},
            "evse": {"id": 1, "connectorId": 1},
        }
        async_to_sync(dispatcher.dispatch)(
            Call(
                unique_id="event-start-reconcile",
                action="TransactionEvent",
                payload=start_payload,
            )
        )

        async_to_sync(dispatcher.dispatch)(
            Call(
                unique_id="status-available-201",
                action="StatusNotification",
                payload={
                    "timestamp": "2026-09-22T10:20:00Z",
                    "connectorStatus": "Available",
                    "evseId": 1,
                    "connectorId": 1,
                },
            )
        )
        selected = OcppTransaction.objects.get(
            charger=self.charger,
            remote_id="reconnect-201",
        )
        self.assertEqual(
            selected.recovery_state,
            OcppTransaction.RecoveryState.UNRESOLVED,
        )

        async_to_sync(dispatcher.dispatch)(
            Call(
                unique_id="event-update-reconcile",
                action="TransactionEvent",
                payload={
                    **start_payload,
                    "eventType": "Updated",
                    "timestamp": "2026-09-22T10:25:00Z",
                    "seqNo": 2,
                },
            )
        )
        selected.refresh_from_db()

        self.assertEqual(
            selected.recovery_state,
            OcppTransaction.RecoveryState.ACTIVE,
        )
        self.assertIsNone(selected.stopped_at)



class TransactionRestartAcceptanceTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("restart-acceptance")

    def _v16_dispatcher(self) -> FrameDispatcher:
        return FrameDispatcher(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            pending_calls=PendingCalls(),
            handler_resolver=InboundActions(self.charger).resolve,
        )

    def _v201_dispatcher(self) -> FrameDispatcher:
        return FrameDispatcher(
            charger=self.charger,
            version=ProtocolVersion.OCPP_201,
            pending_calls=PendingCalls(),
            handler_resolver=Inbound201Actions(self.charger).resolve,
        )

    def test_stale_start_transaction_recovers_after_fresh_dispatcher(self) -> None:
        frame = Call(
            unique_id="restart-start",
            action="StartTransaction",
            payload={
                "connectorId": 1,
                "idTag": "guest-card",
                "meterStart": 100,
                "timestamp": "2026-09-22T10:00:00Z",
            },
        )
        with patch(
            "apps.ocpp.services.transactions.complete_with_result",
            side_effect=RuntimeError("crash before durable response"),
        ):
            with self.assertRaisesRegex(RuntimeError, "crash before durable response"):
                async_to_sync(self._v16_dispatcher().dispatch)(frame)

        self.assertFalse(OcppTransaction.objects.exists())
        request = InboundProtocolRequest.objects.get(
            charger=self.charger,
            action="StartTransaction",
            unique_id="restart-start",
        )
        InboundProtocolRequest.objects.filter(pk=request.pk).update(
            received_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        )

        recovered = async_to_sync(self._v16_dispatcher().dispatch)(frame)

        self.assertIsInstance(recovered, CallResult)
        self.assertEqual(OcppTransaction.objects.count(), 1)
        self.assertEqual(AuthorizationAttempt.objects.count(), 1)
        request.refresh_from_db()
        self.assertEqual(request.status, InboundProtocolRequest.Status.COMPLETED)

    def test_stale_transaction_event_recovers_after_fresh_dispatcher(self) -> None:
        frame = Call(
            unique_id="restart-event",
            action="TransactionEvent",
            payload={
                "eventType": "Started",
                "timestamp": "2026-09-22T10:00:00Z",
                "triggerReason": "Authorized",
                "seqNo": 1,
                "transactionInfo": {"transactionId": "restart-201"},
                "idToken": {"idToken": "guest-card", "type": "Central"},
                "evse": {"id": 1, "connectorId": 1},
            },
        )
        with patch(
            "apps.ocpp.services.transactions.complete_with_result",
            side_effect=RuntimeError("crash before durable response"),
        ):
            with self.assertRaisesRegex(RuntimeError, "crash before durable response"):
                async_to_sync(self._v201_dispatcher().dispatch)(frame)

        self.assertFalse(OcppTransaction.objects.exists())
        request = InboundProtocolRequest.objects.get(
            charger=self.charger,
            action="TransactionEvent",
            unique_id="restart-event",
        )
        InboundProtocolRequest.objects.filter(pk=request.pk).update(
            received_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        )

        recovered = async_to_sync(self._v201_dispatcher().dispatch)(frame)

        self.assertIsInstance(recovered, CallResult)
        self.assertEqual(OcppTransaction.objects.count(), 1)
        self.assertEqual(AuthorizationAttempt.objects.count(), 1)
        request.refresh_from_db()
        self.assertEqual(request.status, InboundProtocolRequest.Status.COMPLETED)

    def test_stop_completion_failure_rolls_back_domain_mutation(self) -> None:
        selected = OcppTransaction.objects.create(
            charger=self.charger,
            remote_id="stop-target",
            started_at=datetime(2026, 9, 22, 10, tzinfo=timezone.utc),
            last_activity_at=datetime(2026, 9, 22, 10, tzinfo=timezone.utc),
            meter_start=100,
        )
        frame = Call(
            unique_id="stop-crash",
            action="StopTransaction",
            payload={
                "transactionId": selected.pk,
                "meterStop": 150,
                "timestamp": "2026-09-22T10:30:00Z",
            },
        )

        with patch(
            "apps.ocpp.services.transactions.complete_with_result",
            side_effect=RuntimeError("crash before durable response"),
        ):
            with self.assertRaisesRegex(RuntimeError, "crash before durable response"):
                async_to_sync(self._v16_dispatcher().dispatch)(frame)

        selected.refresh_from_db()
        self.assertIsNone(selected.stopped_at)
        self.assertEqual(
            selected.recovery_state,
            OcppTransaction.RecoveryState.ACTIVE,
        )
        request = InboundProtocolRequest.objects.get(
            charger=self.charger,
            action="StopTransaction",
            unique_id="stop-crash",
        )
        self.assertEqual(request.status, InboundProtocolRequest.Status.PROCESSING)

    def test_stale_stop_transaction_recovers_after_fresh_dispatcher(self) -> None:
        selected = OcppTransaction.objects.create(
            charger=self.charger,
            remote_id="stop-target",
            started_at=datetime(2026, 9, 22, 10, tzinfo=timezone.utc),
            last_activity_at=datetime(2026, 9, 22, 10, tzinfo=timezone.utc),
            meter_start=100,
        )
        frame = Call(
            unique_id="stale-stop",
            action="StopTransaction",
            payload={
                "transactionId": selected.pk,
                "meterStop": 150,
                "timestamp": "2026-09-22T10:30:00Z",
            },
        )

        with patch(
            "apps.ocpp.services.transactions.complete_with_result",
            side_effect=RuntimeError("crash before durable response"),
        ):
            with self.assertRaisesRegex(RuntimeError, "crash before durable response"):
                async_to_sync(self._v16_dispatcher().dispatch)(frame)

        request = InboundProtocolRequest.objects.get(
            charger=self.charger,
            action="StopTransaction",
            unique_id="stale-stop",
        )
        InboundProtocolRequest.objects.filter(pk=request.pk).update(
            received_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        )

        recovered = async_to_sync(self._v16_dispatcher().dispatch)(frame)

        self.assertIsInstance(recovered, CallResult)
        self.assertEqual(
            recovered.payload,
            {"idTagInfo": {"status": "Accepted"}},
        )
        selected.refresh_from_db()
        self.assertEqual(
            selected.stopped_at,
            datetime(2026, 9, 22, 10, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(
            selected.recovery_state,
            OcppTransaction.RecoveryState.COMPLETED,
        )
        self.assertEqual(str(selected.energy_kwh), "0.0500")
        request.refresh_from_db()
        self.assertEqual(request.status, InboundProtocolRequest.Status.COMPLETED)

        replayed = async_to_sync(self._v16_dispatcher().dispatch)(frame)
        self.assertEqual(replayed, recovered)

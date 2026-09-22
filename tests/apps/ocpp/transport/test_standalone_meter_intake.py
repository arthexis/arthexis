from datetime import timedelta
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.events.models import EventEnvelope
from apps.ocpp.models import InboundProtocolRequest, MeterReadingBatch
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallError, CallResult
from apps.ocpp.protocol.v201.inbound import InboundActions
from apps.ocpp.transport.dispatch import FrameDispatcher
from tests.apps.ocpp.builders import charger


class StandaloneMeterIntakeTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self) -> None:
        self.charger = charger("standalone-meter")

    def _dispatcher(self) -> FrameDispatcher:
        return FrameDispatcher(
            charger=self.charger,
            version=ProtocolVersion.OCPP_201,
            pending_calls=PendingCalls(),
            handler_resolver=InboundActions(self.charger).resolve,
        )

    @staticmethod
    def _payload(*, value: int = 10, timestamp: str = "2026-09-22T18:00:00Z"):
        return {
            "evseId": 2,
            "meterValue": [
                {
                    "timestamp": timestamp,
                    "sampledValue": [
                        {
                            "value": value,
                            "measurand": "Power.Active.Import",
                            "unitOfMeasure": {"unit": "W"},
                        }
                    ],
                }
            ],
        }

    def test_standalone_meter_values_persist_ack_and_enqueue(self) -> None:
        response = async_to_sync(self._dispatcher().dispatch)(
            Call(
                unique_id="meter-1",
                action="MeterValues",
                payload=self._payload(),
            )
        )

        self.assertEqual(response, CallResult(unique_id="meter-1", payload={}))
        batch = MeterReadingBatch.objects.get()
        self.assertEqual(batch.charger, self.charger)
        self.assertEqual(batch.protocol, "ocpp2.0.1")
        self.assertEqual(batch.evse_id, 2)
        self.assertEqual(
            batch.reported_at.isoformat(),
            "2026-09-22T18:00:00+00:00",
        )
        self.assertEqual(batch.payload["meterValue"][0]["sampledValue"][0]["value"], 10)

        replay = InboundProtocolRequest.objects.get(
            charger=self.charger,
            action="MeterValues",
            unique_id="meter-1",
        )
        self.assertEqual(replay.status, InboundProtocolRequest.Status.COMPLETED)
        self.assertEqual(replay.response_payload, {})

        event = EventEnvelope.objects.get(event_type="ocpp.meter_values.received")
        self.assertEqual(event.payload["meter_batch_id"], batch.pk)
        self.assertEqual(event.payload["charger_id"], self.charger.pk)
        self.assertIsNone(event.payload["transaction_id"])
        self.assertEqual(event.payload["evse_id"], 2)

    def test_exact_replay_does_not_duplicate_batch_or_event(self) -> None:
        frame = Call(
            unique_id="meter-replay",
            action="MeterValues",
            payload=self._payload(),
        )

        first = async_to_sync(self._dispatcher().dispatch)(frame)
        replayed = async_to_sync(self._dispatcher().dispatch)(frame)

        self.assertEqual(replayed, first)
        self.assertEqual(MeterReadingBatch.objects.count(), 1)
        self.assertEqual(EventEnvelope.objects.count(), 1)

    def test_reused_call_id_with_different_payload_is_distinct(self) -> None:
        first = Call(
            unique_id="meter-reused",
            action="MeterValues",
            payload=self._payload(value=10),
        )
        second = Call(
            unique_id="meter-reused",
            action="MeterValues",
            payload=self._payload(value=20),
        )

        async_to_sync(self._dispatcher().dispatch)(first)
        async_to_sync(self._dispatcher().dispatch)(second)

        self.assertEqual(MeterReadingBatch.objects.count(), 2)
        self.assertEqual(
            InboundProtocolRequest.objects.filter(
                charger=self.charger,
                action="MeterValues",
                unique_id="meter-reused",
            ).count(),
            2,
        )

    def test_replay_completion_failure_rolls_back_meter_batch(self) -> None:
        with patch(
            "apps.ocpp.services.metering.complete_with_result",
            side_effect=RuntimeError("replay persistence failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "replay persistence failed"):
                async_to_sync(self._dispatcher().dispatch)(
                    Call(
                        unique_id="meter-failure",
                        action="MeterValues",
                        payload=self._payload(),
                    )
                )

        self.assertFalse(MeterReadingBatch.objects.exists())
        self.assertFalse(EventEnvelope.objects.exists())
        replay = InboundProtocolRequest.objects.get(
            charger=self.charger,
            action="MeterValues",
            unique_id="meter-failure",
        )
        self.assertEqual(replay.status, InboundProtocolRequest.Status.PROCESSING)

    def test_secondary_enqueue_failure_does_not_change_ack(self) -> None:
        with patch(
            "apps.ocpp.services.metering.publish_safely",
            side_effect=RuntimeError("secondary enqueue failed"),
        ):
            response = async_to_sync(self._dispatcher().dispatch)(
                Call(
                    unique_id="meter-secondary",
                    action="MeterValues",
                    payload=self._payload(),
                )
            )

        self.assertEqual(response, CallResult(unique_id="meter-secondary", payload={}))
        self.assertEqual(MeterReadingBatch.objects.count(), 1)
        self.assertFalse(EventEnvelope.objects.exists())
        replay = InboundProtocolRequest.objects.get(
            charger=self.charger,
            action="MeterValues",
            unique_id="meter-secondary",
        )
        self.assertEqual(replay.status, InboundProtocolRequest.Status.COMPLETED)

    @override_settings(
        OCPP_REPLAY_STALE_SECONDS=1,
        OCPP_REPLAY_WINDOW_SECONDS=60,
    )
    def test_stale_meter_request_recovers_after_fresh_dispatcher(self) -> None:
        frame = Call(
            unique_id="meter-restart",
            action="MeterValues",
            payload=self._payload(),
        )
        with patch(
            "apps.ocpp.services.metering.complete_with_result",
            side_effect=RuntimeError("crash before replay completion"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "crash before replay completion",
            ):
                async_to_sync(self._dispatcher().dispatch)(frame)

        replay = InboundProtocolRequest.objects.get(
            charger=self.charger,
            action="MeterValues",
            unique_id="meter-restart",
        )
        InboundProtocolRequest.objects.filter(pk=replay.pk).update(
            received_at=timezone.now() - timedelta(seconds=2),
        )

        recovered = async_to_sync(self._dispatcher().dispatch)(frame)

        self.assertEqual(recovered, CallResult(unique_id="meter-restart", payload={}))
        self.assertEqual(MeterReadingBatch.objects.count(), 1)
        self.assertEqual(EventEnvelope.objects.count(), 1)
        replay.refresh_from_db()
        self.assertEqual(replay.status, InboundProtocolRequest.Status.COMPLETED)
        self.assertIsNone(replay.stale_at)

    def test_invalid_meter_payload_fails_before_ack(self) -> None:
        response = async_to_sync(self._dispatcher().dispatch)(
            Call(
                unique_id="meter-invalid",
                action="MeterValues",
                payload={"evseId": 2, "meterValue": [{"sampledValue": []}]},
            )
        )

        self.assertIsInstance(response, CallError)
        self.assertEqual(response.code, "FormationViolation")
        self.assertFalse(MeterReadingBatch.objects.exists())

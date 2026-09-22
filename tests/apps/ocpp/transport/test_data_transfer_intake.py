from datetime import datetime, timezone
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import TransactionTestCase

from apps.events.models import EventEnvelope
from apps.ocpp.models import InboundProtocolRequest, NotificationRecord
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallResult
from apps.ocpp.protocol.v16.inbound import InboundActions as Inbound16Actions
from apps.ocpp.protocol.v201.inbound import InboundActions as Inbound201Actions
from apps.ocpp.transport.dispatch import FrameDispatcher
from tests.apps.ocpp.builders import charger


class DataTransferPersistAndAckTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self) -> None:
        self.charger = charger("data-transfer")

    def _dispatcher(self, version: ProtocolVersion) -> FrameDispatcher:
        resolver = (
            Inbound16Actions(self.charger).resolve
            if version is ProtocolVersion.OCPP_16
            else Inbound201Actions(self.charger).resolve
        )
        return FrameDispatcher(
            charger=self.charger,
            version=version,
            pending_calls=PendingCalls(),
            handler_resolver=resolver,
        )

    @staticmethod
    def _frame(unique_id: str = "transfer-1") -> Call:
        return Call(
            unique_id=unique_id,
            action="DataTransfer",
            payload={
                "vendorId": "ACME",
                "messageId": "telemetry",
                "data": {"value": 42},
            },
        )

    def test_both_versions_persist_ack_and_enqueue_secondary_event(self) -> None:
        for version in (ProtocolVersion.OCPP_16, ProtocolVersion.OCPP_201):
            with self.subTest(version=version):
                self._clear_records()
                response = async_to_sync(self._dispatcher(version).dispatch)(
                    self._frame()
                )

                self.assertEqual(
                    response,
                    CallResult(
                        unique_id="transfer-1",
                        payload={"status": "Accepted"},
                    ),
                )
                retained = NotificationRecord.objects.get(
                    charger=self.charger,
                    action="DataTransfer",
                )
                self.assertEqual(retained.payload["vendorId"], "ACME")
                replay = InboundProtocolRequest.objects.get(
                    charger=self.charger,
                    version=version.value,
                    action="DataTransfer",
                )
                self.assertEqual(
                    replay.status,
                    InboundProtocolRequest.Status.COMPLETED,
                )
                event = EventEnvelope.objects.get(
                    event_type="ocpp.data_transfer.received"
                )
                self.assertEqual(event.delivery_status, EventEnvelope.DeliveryStatus.PENDING)
                self.assertEqual(event.payload["notification_id"], retained.pk)
                self.assertEqual(event.payload["charger_id"], self.charger.pk)

    def test_completed_replay_does_not_duplicate_intake_or_event(self) -> None:
        first = async_to_sync(
            self._dispatcher(ProtocolVersion.OCPP_16).dispatch
        )(self._frame())
        replayed = async_to_sync(
            self._dispatcher(ProtocolVersion.OCPP_16).dispatch
        )(self._frame())

        self.assertEqual(replayed, first)
        self.assertEqual(NotificationRecord.objects.count(), 1)
        self.assertEqual(EventEnvelope.objects.count(), 1)

    def test_replay_completion_failure_rolls_back_durable_intake(self) -> None:
        with patch(
            "apps.ocpp.services.intake.complete_with_result",
            side_effect=RuntimeError("replay persistence failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "replay persistence failed"):
                async_to_sync(
                    self._dispatcher(ProtocolVersion.OCPP_16).dispatch
                )(self._frame("transfer-failure"))

        self.assertFalse(NotificationRecord.objects.exists())
        self.assertFalse(EventEnvelope.objects.exists())
        replay = InboundProtocolRequest.objects.get(
            charger=self.charger,
            action="DataTransfer",
            unique_id="transfer-failure",
        )
        self.assertEqual(replay.status, InboundProtocolRequest.Status.PROCESSING)

    def test_secondary_enqueue_failure_does_not_change_accepted_response(self) -> None:
        with patch(
            "apps.ocpp.services.intake.publish_safely",
            side_effect=RuntimeError("secondary enqueue failed"),
        ):
            response = async_to_sync(
                self._dispatcher(ProtocolVersion.OCPP_16).dispatch
            )(self._frame("transfer-secondary-failure"))

        self.assertEqual(
            response,
            CallResult(
                unique_id="transfer-secondary-failure",
                payload={"status": "Accepted"},
            ),
        )
        self.assertEqual(NotificationRecord.objects.count(), 1)
        self.assertFalse(EventEnvelope.objects.exists())
        replay = InboundProtocolRequest.objects.get(
            charger=self.charger,
            action="DataTransfer",
            unique_id="transfer-secondary-failure",
        )
        self.assertEqual(replay.status, InboundProtocolRequest.Status.COMPLETED)

    def test_stale_processing_request_recovers_after_fresh_dispatcher(self) -> None:
        frame = self._frame("transfer-restart")
        with patch(
            "apps.ocpp.services.intake.complete_with_result",
            side_effect=RuntimeError("crash before replay completion"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "crash before replay completion",
            ):
                async_to_sync(
                    self._dispatcher(ProtocolVersion.OCPP_201).dispatch
                )(frame)

        replay = InboundProtocolRequest.objects.get(
            charger=self.charger,
            action="DataTransfer",
            unique_id="transfer-restart",
        )
        InboundProtocolRequest.objects.filter(pk=replay.pk).update(
            received_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        )

        recovered = async_to_sync(
            self._dispatcher(ProtocolVersion.OCPP_201).dispatch
        )(frame)

        self.assertEqual(
            recovered,
            CallResult(
                unique_id="transfer-restart",
                payload={"status": "Accepted"},
            ),
        )
        self.assertEqual(NotificationRecord.objects.count(), 1)
        self.assertEqual(EventEnvelope.objects.count(), 1)
        replay.refresh_from_db()
        self.assertEqual(replay.status, InboundProtocolRequest.Status.COMPLETED)
        self.assertIsNone(replay.stale_at)

    def _clear_records(self) -> None:
        EventEnvelope.objects.all().delete()
        InboundProtocolRequest.objects.all().delete()
        NotificationRecord.objects.all().delete()

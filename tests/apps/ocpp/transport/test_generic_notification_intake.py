from datetime import timedelta
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.events.models import EventEnvelope
from apps.ocpp.models import InboundProtocolRequest, NotificationRecord
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallResult
from apps.ocpp.protocol.v201.inbound import InboundActions
from apps.ocpp.transport.dispatch import FrameDispatcher
from tests.apps.ocpp.builders import charger


class GenericNotificationPersistAndAckTests(TransactionTestCase):
    reset_sequences = True

    RESPONSES = {
        "ClearedChargingLimit": {},
        "CostUpdated": {},
        "NotifyChargingLimit": {},
        "NotifyCustomerInformation": {},
        "NotifyDisplayMessages": {},
        "NotifyEVChargingNeeds": {"status": "Accepted"},
        "NotifyEVChargingSchedule": {"status": "Accepted"},
        "NotifyEvent": {},
        "ReservationStatusUpdate": {},
        "SecurityEventNotification": {},
    }

    def setUp(self) -> None:
        self.charger = charger("notification-intake")

    def _dispatcher(self) -> FrameDispatcher:
        return FrameDispatcher(
            charger=self.charger,
            version=ProtocolVersion.OCPP_201,
            pending_calls=PendingCalls(),
            handler_resolver=InboundActions(self.charger).resolve,
        )

    def test_all_generic_notifications_persist_exact_ack_and_enqueue(self) -> None:
        for action, expected in self.RESPONSES.items():
            with self.subTest(action=action):
                self._clear_records()
                response = async_to_sync(self._dispatcher().dispatch)(
                    Call(
                        unique_id=f"{action}-1",
                        action=action,
                        payload={"customData": {"source": "test"}},
                    )
                )

                self.assertEqual(
                    response,
                    CallResult(
                        unique_id=f"{action}-1",
                        payload=expected,
                    ),
                )
                retained = NotificationRecord.objects.get(
                    charger=self.charger,
                    action=action,
                )
                self.assertEqual(
                    retained.payload["customData"]["source"],
                    "test",
                )
                self.assertIsNone(retained.reported_at)

                replay = InboundProtocolRequest.objects.get(
                    charger=self.charger,
                    version=ProtocolVersion.OCPP_201.value,
                    action=action,
                )
                self.assertEqual(
                    replay.status,
                    InboundProtocolRequest.Status.COMPLETED,
                )
                self.assertEqual(replay.response_payload, expected)

                event = EventEnvelope.objects.get(
                    event_type="ocpp.notification.received"
                )
                self.assertEqual(
                    event.payload["notification_id"],
                    retained.pk,
                )
                self.assertEqual(event.payload["action"], action)

    def test_completed_replay_does_not_duplicate_intake_or_event(self) -> None:
        frame = Call(
            unique_id="notify-event-replay",
            action="NotifyEvent",
            payload={"generatedAt": "test"},
        )

        first = async_to_sync(self._dispatcher().dispatch)(frame)
        replayed = async_to_sync(self._dispatcher().dispatch)(frame)

        self.assertEqual(replayed, first)
        self.assertEqual(NotificationRecord.objects.count(), 1)
        self.assertEqual(EventEnvelope.objects.count(), 1)

    def test_direct_timestamp_is_preserved(self) -> None:
        timestamp = timezone.now().replace(microsecond=0)

        async_to_sync(self._dispatcher().dispatch)(
            Call(
                unique_id="security-timestamp",
                action="SecurityEventNotification",
                payload={
                    "timestamp": timestamp.isoformat(),
                    "type": "TamperDetectionActivated",
                },
            )
        )

        self.assertEqual(
            NotificationRecord.objects.get().reported_at,
            timestamp,
        )

    def test_notify_event_preserves_latest_event_data_timestamp(self) -> None:
        older = timezone.now().replace(microsecond=0) - timedelta(minutes=2)
        newer = older + timedelta(minutes=1)

        async_to_sync(self._dispatcher().dispatch)(
            Call(
                unique_id="notify-event-timestamps",
                action="NotifyEvent",
                payload={
                    "eventData": [
                        {"timestamp": older.isoformat(), "eventId": 1},
                        {"timestamp": newer.isoformat(), "eventId": 2},
                    ]
                },
            )
        )

        self.assertEqual(
            NotificationRecord.objects.get().reported_at,
            newer,
        )

    def test_replay_completion_failure_rolls_back_notification(self) -> None:
        with patch(
            "apps.ocpp.services.intake.complete_with_result",
            side_effect=RuntimeError("replay persistence failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "replay persistence failed"):
                async_to_sync(self._dispatcher().dispatch)(
                    Call(
                        unique_id="notify-failure",
                        action="NotifyChargingLimit",
                        payload={},
                    )
                )

        self.assertFalse(NotificationRecord.objects.exists())
        self.assertFalse(EventEnvelope.objects.exists())
        replay = InboundProtocolRequest.objects.get(
            charger=self.charger,
            action="NotifyChargingLimit",
            unique_id="notify-failure",
        )
        self.assertEqual(
            replay.status,
            InboundProtocolRequest.Status.PROCESSING,
        )

    def test_secondary_enqueue_failure_does_not_change_ack(self) -> None:
        with patch(
            "apps.ocpp.services.intake.publish_safely",
            side_effect=RuntimeError("secondary enqueue failed"),
        ):
            response = async_to_sync(self._dispatcher().dispatch)(
                Call(
                    unique_id="needs-secondary-failure",
                    action="NotifyEVChargingNeeds",
                    payload={},
                )
            )

        self.assertEqual(
            response,
            CallResult(
                unique_id="needs-secondary-failure",
                payload={"status": "Accepted"},
            ),
        )
        self.assertEqual(NotificationRecord.objects.count(), 1)
        self.assertFalse(EventEnvelope.objects.exists())
        replay = InboundProtocolRequest.objects.get(
            charger=self.charger,
            action="NotifyEVChargingNeeds",
            unique_id="needs-secondary-failure",
        )
        self.assertEqual(
            replay.status,
            InboundProtocolRequest.Status.COMPLETED,
        )

    @override_settings(
        OCPP_REPLAY_STALE_SECONDS=1,
        OCPP_REPLAY_WINDOW_SECONDS=60,
    )
    def test_stale_notification_recovers_after_fresh_dispatcher(self) -> None:
        frame = Call(
            unique_id="notify-restart",
            action="NotifyCustomerInformation",
            payload={"data": "retained"},
        )
        with patch(
            "apps.ocpp.services.intake.complete_with_result",
            side_effect=RuntimeError("crash before replay completion"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "crash before replay completion",
            ):
                async_to_sync(self._dispatcher().dispatch)(frame)

        replay = InboundProtocolRequest.objects.get(
            charger=self.charger,
            action="NotifyCustomerInformation",
            unique_id="notify-restart",
        )
        InboundProtocolRequest.objects.filter(pk=replay.pk).update(
            received_at=timezone.now() - timedelta(seconds=2),
        )

        recovered = async_to_sync(self._dispatcher().dispatch)(frame)

        self.assertEqual(
            recovered,
            CallResult(unique_id="notify-restart", payload={}),
        )
        self.assertEqual(NotificationRecord.objects.count(), 1)
        self.assertEqual(EventEnvelope.objects.count(), 1)
        replay.refresh_from_db()
        self.assertEqual(
            replay.status,
            InboundProtocolRequest.Status.COMPLETED,
        )
        self.assertIsNone(replay.stale_at)

    def _clear_records(self) -> None:
        EventEnvelope.objects.all().delete()
        InboundProtocolRequest.objects.all().delete()
        NotificationRecord.objects.all().delete()

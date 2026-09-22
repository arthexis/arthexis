from datetime import timedelta
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.events.models import EventEnvelope
from apps.ocpp.models import (
    InboundProtocolRequest,
    MonitoringRecord,
    NotificationRecord,
)
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallResult
from apps.ocpp.protocol.v201.inbound import InboundActions
from apps.ocpp.transport.dispatch import FrameDispatcher
from tests.apps.ocpp.builders import charger


class ReportPersistAndAckTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self) -> None:
        self.charger = charger("report-intake")

    def _dispatcher(self) -> FrameDispatcher:
        return FrameDispatcher(
            charger=self.charger,
            version=ProtocolVersion.OCPP_201,
            pending_calls=PendingCalls(),
            handler_resolver=InboundActions(self.charger).resolve,
        )

    def test_all_report_actions_persist_ack_and_enqueue(self) -> None:
        cases = (
            (
                "NotifyMonitoringReport",
                {
                    "requestId": 10,
                    "seqNo": 1,
                    "generatedAt": "2026-09-22T18:00:00Z",
                    "tbc": True,
                },
                "monitoring",
            ),
            (
                "NotifyReport",
                {
                    "requestId": 11,
                    "seqNo": 2,
                    "generatedAt": "2026-09-22T18:01:00Z",
                    "tbc": False,
                },
                "monitoring",
            ),
            (
                "ReportChargingProfiles",
                {
                    "requestId": 12,
                    "evseId": 1,
                    "tbc": False,
                },
                "notification",
            ),
        )

        for action, payload, record_type in cases:
            with self.subTest(action=action):
                self._clear_records()
                response = async_to_sync(self._dispatcher().dispatch)(
                    Call(
                        unique_id=f"{action}-1",
                        action=action,
                        payload=payload,
                    )
                )

                self.assertEqual(
                    response,
                    CallResult(unique_id=f"{action}-1", payload={}),
                )
                replay = InboundProtocolRequest.objects.get(
                    charger=self.charger,
                    action=action,
                )
                self.assertEqual(
                    replay.status,
                    InboundProtocolRequest.Status.COMPLETED,
                )

                if record_type == "monitoring":
                    retained = MonitoringRecord.objects.get(event_type=action)
                else:
                    retained = NotificationRecord.objects.get(action=action)

                event = EventEnvelope.objects.get(
                    event_type="ocpp.report.received"
                )
                self.assertEqual(event.payload["record_id"], retained.pk)
                self.assertEqual(event.payload["record_type"], record_type)
                self.assertEqual(event.payload["action"], action)
                self.assertEqual(
                    event.payload["request_id"],
                    payload["requestId"],
                )

    def test_same_sequenced_chunk_new_call_id_is_replayed_without_duplication(self) -> None:
        payload = {
            "requestId": 42,
            "seqNo": 3,
            "generatedAt": "2026-09-22T18:02:00Z",
            "tbc": True,
        }

        first = async_to_sync(self._dispatcher().dispatch)(
            Call(
                unique_id="report-call-1",
                action="NotifyReport",
                payload=payload,
            )
        )
        replayed = async_to_sync(self._dispatcher().dispatch)(
            Call(
                unique_id="report-call-2",
                action="NotifyReport",
                payload=payload,
            )
        )

        self.assertEqual(first.payload, {})
        self.assertEqual(replayed.payload, {})
        self.assertEqual(replayed.unique_id, "report-call-2")
        self.assertEqual(MonitoringRecord.objects.count(), 1)
        self.assertEqual(EventEnvelope.objects.count(), 1)
        self.assertEqual(
            InboundProtocolRequest.objects.filter(
                charger=self.charger,
                action="NotifyReport",
            ).count(),
            1,
        )

    def test_next_sequence_is_a_distinct_chunk(self) -> None:
        base = {
            "requestId": 42,
            "generatedAt": "2026-09-22T18:02:00Z",
            "tbc": True,
        }

        async_to_sync(self._dispatcher().dispatch)(
            Call(
                unique_id="report-seq-1",
                action="NotifyMonitoringReport",
                payload={**base, "seqNo": 1},
            )
        )
        async_to_sync(self._dispatcher().dispatch)(
            Call(
                unique_id="report-seq-2",
                action="NotifyMonitoringReport",
                payload={**base, "seqNo": 2},
            )
        )

        self.assertEqual(MonitoringRecord.objects.count(), 2)
        self.assertEqual(EventEnvelope.objects.count(), 2)

    def test_generated_at_is_preserved_as_reported_at(self) -> None:
        expected = timezone.datetime(
            2026,
            9,
            22,
            18,
            3,
            tzinfo=timezone.utc,
        )

        async_to_sync(self._dispatcher().dispatch)(
            Call(
                unique_id="report-timestamp",
                action="NotifyReport",
                payload={
                    "requestId": 77,
                    "seqNo": 1,
                    "generatedAt": expected.isoformat(),
                },
            )
        )

        self.assertEqual(
            MonitoringRecord.objects.get().reported_at,
            expected,
        )

    def test_unsequenced_profile_reports_with_different_call_ids_are_distinct(self) -> None:
        payload = {
            "requestId": 88,
            "evseId": 1,
            "tbc": True,
        }

        async_to_sync(self._dispatcher().dispatch)(
            Call(
                unique_id="profile-call-1",
                action="ReportChargingProfiles",
                payload=payload,
            )
        )
        async_to_sync(self._dispatcher().dispatch)(
            Call(
                unique_id="profile-call-2",
                action="ReportChargingProfiles",
                payload=payload,
            )
        )

        self.assertEqual(NotificationRecord.objects.count(), 2)

    def test_replay_completion_failure_rolls_back_report_intake(self) -> None:
        with patch(
            "apps.ocpp.services.intake.complete_with_result",
            side_effect=RuntimeError("replay persistence failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "replay persistence failed"):
                async_to_sync(self._dispatcher().dispatch)(
                    Call(
                        unique_id="report-failure",
                        action="NotifyReport",
                        payload={"requestId": 1, "seqNo": 1},
                    )
                )

        self.assertFalse(MonitoringRecord.objects.exists())
        self.assertFalse(EventEnvelope.objects.exists())
        replay = InboundProtocolRequest.objects.get(
            charger=self.charger,
            action="NotifyReport",
            unique_id="report-failure",
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
                    unique_id="monitoring-secondary-failure",
                    action="NotifyMonitoringReport",
                    payload={"requestId": 2, "seqNo": 1},
                )
            )

        self.assertEqual(
            response,
            CallResult(
                unique_id="monitoring-secondary-failure",
                payload={},
            ),
        )
        self.assertEqual(MonitoringRecord.objects.count(), 1)
        self.assertFalse(EventEnvelope.objects.exists())

    @override_settings(
        OCPP_REPLAY_STALE_SECONDS=1,
        OCPP_REPLAY_WINDOW_SECONDS=60,
    )
    def test_stale_report_recovers_after_fresh_dispatcher(self) -> None:
        frame = Call(
            unique_id="report-restart",
            action="NotifyReport",
            payload={"requestId": 3, "seqNo": 1, "tbc": False},
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
            action="NotifyReport",
            unique_id="report-restart",
        )
        InboundProtocolRequest.objects.filter(pk=replay.pk).update(
            received_at=timezone.now() - timedelta(seconds=2),
        )

        recovered = async_to_sync(self._dispatcher().dispatch)(frame)

        self.assertEqual(
            recovered,
            CallResult(unique_id="report-restart", payload={}),
        )
        self.assertEqual(MonitoringRecord.objects.count(), 1)
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
        MonitoringRecord.objects.all().delete()
        NotificationRecord.objects.all().delete()

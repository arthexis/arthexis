from datetime import timedelta
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.events.models import EventEnvelope
from apps.ocpp.models import InboundProtocolRequest, OperationalStatusRecord
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallResult
from apps.ocpp.protocol.v16.inbound import InboundActions as Inbound16Actions
from apps.ocpp.protocol.v201.inbound import InboundActions as Inbound201Actions
from apps.ocpp.transport.dispatch import FrameDispatcher
from tests.apps.ocpp.builders import charger


class OperationalStatusPersistAndAckTests(TransactionTestCase):
    reset_sequences = True

    ACTIONS = (
        (
            ProtocolVersion.OCPP_16,
            "DiagnosticsStatusNotification",
            "Uploaded",
            OperationalStatusRecord.Kind.DIAGNOSTICS,
        ),
        (
            ProtocolVersion.OCPP_16,
            "FirmwareStatusNotification",
            "Downloaded",
            OperationalStatusRecord.Kind.FIRMWARE,
        ),
        (
            ProtocolVersion.OCPP_201,
            "FirmwareStatusNotification",
            "Downloaded",
            OperationalStatusRecord.Kind.FIRMWARE,
        ),
        (
            ProtocolVersion.OCPP_201,
            "PublishFirmwareStatusNotification",
            "Published",
            OperationalStatusRecord.Kind.FIRMWARE,
        ),
        (
            ProtocolVersion.OCPP_201,
            "LogStatusNotification",
            "Uploaded",
            OperationalStatusRecord.Kind.LOG,
        ),
    )

    def setUp(self) -> None:
        self.charger = charger("status-intake")

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

    def test_all_operational_status_actions_persist_ack_and_enqueue(self) -> None:
        for version, action, status, kind in self.ACTIONS:
            with self.subTest(version=version, action=action):
                self._clear_records()
                response = async_to_sync(self._dispatcher(version).dispatch)(
                    Call(
                        unique_id=f"{action}-1",
                        action=action,
                        payload={"status": status},
                    )
                )

                self.assertEqual(
                    response,
                    CallResult(unique_id=f"{action}-1", payload={}),
                )
                retained = OperationalStatusRecord.objects.get()
                self.assertEqual(retained.kind, kind)
                self.assertEqual(retained.status, status)
                self.assertEqual(retained.source_action, action)
                self.assertIsNone(retained.reported_at)

                replay = InboundProtocolRequest.objects.get(
                    charger=self.charger,
                    version=version.value,
                    action=action,
                )
                self.assertEqual(
                    replay.status,
                    InboundProtocolRequest.Status.COMPLETED,
                )
                event = EventEnvelope.objects.get(
                    event_type="ocpp.operational_status.received"
                )
                self.assertEqual(
                    event.payload["operational_status_id"],
                    retained.pk,
                )
                self.assertEqual(event.payload["action"], action)
                self.assertEqual(event.payload["kind"], kind)

    def test_optional_reported_timestamp_is_preserved(self) -> None:
        timestamp = timezone.now().replace(microsecond=0)
        response = async_to_sync(
            self._dispatcher(ProtocolVersion.OCPP_201).dispatch
        )(
            Call(
                unique_id="firmware-timestamp",
                action="FirmwareStatusNotification",
                payload={
                    "status": "Downloaded",
                    "timestamp": timestamp.isoformat(),
                },
            )
        )

        self.assertIsInstance(response, CallResult)
        self.assertEqual(
            OperationalStatusRecord.objects.get().reported_at,
            timestamp,
        )

    def test_replay_completion_failure_rolls_back_status_intake(self) -> None:
        with patch(
            "apps.ocpp.services.intake.complete_with_result",
            side_effect=RuntimeError("replay persistence failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "replay persistence failed"):
                async_to_sync(
                    self._dispatcher(ProtocolVersion.OCPP_16).dispatch
                )(
                    Call(
                        unique_id="firmware-failure",
                        action="FirmwareStatusNotification",
                        payload={"status": "Downloaded"},
                    )
                )

        self.assertFalse(OperationalStatusRecord.objects.exists())
        self.assertFalse(EventEnvelope.objects.exists())
        replay = InboundProtocolRequest.objects.get(
            charger=self.charger,
            action="FirmwareStatusNotification",
            unique_id="firmware-failure",
        )
        self.assertEqual(replay.status, InboundProtocolRequest.Status.PROCESSING)

    def test_secondary_enqueue_failure_does_not_change_ack(self) -> None:
        with patch(
            "apps.ocpp.services.intake.publish_safely",
            side_effect=RuntimeError("secondary enqueue failed"),
        ):
            response = async_to_sync(
                self._dispatcher(ProtocolVersion.OCPP_201).dispatch
            )(
                Call(
                    unique_id="log-secondary-failure",
                    action="LogStatusNotification",
                    payload={"status": "Uploaded"},
                )
            )

        self.assertEqual(
            response,
            CallResult(unique_id="log-secondary-failure", payload={}),
        )
        self.assertEqual(OperationalStatusRecord.objects.count(), 1)
        self.assertFalse(EventEnvelope.objects.exists())
        replay = InboundProtocolRequest.objects.get(
            charger=self.charger,
            action="LogStatusNotification",
            unique_id="log-secondary-failure",
        )
        self.assertEqual(replay.status, InboundProtocolRequest.Status.COMPLETED)

    @override_settings(
        OCPP_REPLAY_STALE_SECONDS=1,
        OCPP_REPLAY_WINDOW_SECONDS=60,
    )
    def test_stale_status_recovers_after_fresh_dispatcher(self) -> None:
        frame = Call(
            unique_id="firmware-restart",
            action="FirmwareStatusNotification",
            payload={"status": "Downloaded"},
        )
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
            action="FirmwareStatusNotification",
            unique_id="firmware-restart",
        )
        InboundProtocolRequest.objects.filter(pk=replay.pk).update(
            received_at=timezone.now() - timedelta(seconds=2),
        )

        recovered = async_to_sync(
            self._dispatcher(ProtocolVersion.OCPP_201).dispatch
        )(frame)

        self.assertEqual(
            recovered,
            CallResult(unique_id="firmware-restart", payload={}),
        )
        self.assertEqual(OperationalStatusRecord.objects.count(), 1)
        self.assertEqual(EventEnvelope.objects.count(), 1)
        replay.refresh_from_db()
        self.assertEqual(replay.status, InboundProtocolRequest.Status.COMPLETED)
        self.assertIsNone(replay.stale_at)

    def _clear_records(self) -> None:
        EventEnvelope.objects.all().delete()
        InboundProtocolRequest.objects.all().delete()
        OperationalStatusRecord.objects.all().delete()

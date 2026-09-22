from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.ocpp.models import InboundProtocolRequest, NotificationRecord
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallResult
from apps.ocpp.protocol.v16.inbound import InboundActions as Inbound16Actions
from apps.ocpp.protocol.v201.inbound import InboundActions as Inbound201Actions
from apps.ocpp.transport.dispatch import FrameDispatcher
from tests.apps.ocpp.builders import charger


class DurableReplayRestartTests(TestCase):
    def test_ocpp16_replay_survives_fresh_dispatcher_instance(self) -> None:
        selected = charger("restart-16")
        frame = Call(
            unique_id="transfer-1",
            action="DataTransfer",
            payload={"vendorId": "ACME", "data": "payload"},
        )

        first_dispatcher = FrameDispatcher(
            charger=selected,
            version=ProtocolVersion.OCPP_16,
            pending_calls=PendingCalls(),
            handler_resolver=Inbound16Actions(selected).resolve,
        )
        first = async_to_sync(first_dispatcher.dispatch)(frame)

        second_dispatcher = FrameDispatcher(
            charger=selected,
            version=ProtocolVersion.OCPP_16,
            pending_calls=PendingCalls(),
            handler_resolver=Inbound16Actions(selected).resolve,
        )
        replayed = async_to_sync(second_dispatcher.dispatch)(frame)

        self.assertEqual(
            first,
            CallResult(unique_id="transfer-1", payload={"status": "Accepted"}),
        )
        self.assertEqual(replayed, first)
        self.assertEqual(
            NotificationRecord.objects.filter(
                charger=selected,
                action="DataTransfer",
            ).count(),
            1,
        )
        request = InboundProtocolRequest.objects.get(
            charger=selected,
            unique_id="transfer-1",
        )
        self.assertEqual(request.status, InboundProtocolRequest.Status.COMPLETED)

    def test_ocpp201_replay_survives_fresh_dispatcher_instance(self) -> None:
        selected = charger("restart-201")
        frame = Call(
            unique_id="notify-1",
            action="NotifyEvent",
            payload={
                "generatedAt": "2026-09-22T15:00:00Z",
                "seqNo": 1,
                "eventData": [],
            },
        )

        first_dispatcher = FrameDispatcher(
            charger=selected,
            version=ProtocolVersion.OCPP_201,
            pending_calls=PendingCalls(),
            handler_resolver=Inbound201Actions(selected).resolve,
        )
        first = async_to_sync(first_dispatcher.dispatch)(frame)

        second_dispatcher = FrameDispatcher(
            charger=selected,
            version=ProtocolVersion.OCPP_201,
            pending_calls=PendingCalls(),
            handler_resolver=Inbound201Actions(selected).resolve,
        )
        replayed = async_to_sync(second_dispatcher.dispatch)(frame)

        self.assertEqual(first, CallResult(unique_id="notify-1", payload={}))
        self.assertEqual(replayed, first)
        self.assertEqual(
            NotificationRecord.objects.filter(
                charger=selected,
                action="NotifyEvent",
            ).count(),
            1,
        )
        request = InboundProtocolRequest.objects.get(
            charger=selected,
            unique_id="notify-1",
        )
        self.assertEqual(request.version, ProtocolVersion.OCPP_201.value)

    def test_same_call_id_isolated_between_chargers_after_restart(self) -> None:
        first_charger = charger("restart-a")
        second_charger = charger("restart-b")
        frame = Call(
            unique_id="shared-call",
            action="DataTransfer",
            payload={"vendorId": "ACME"},
        )

        first_dispatcher = FrameDispatcher(
            charger=first_charger,
            version=ProtocolVersion.OCPP_16,
            pending_calls=PendingCalls(),
            handler_resolver=Inbound16Actions(first_charger).resolve,
        )
        second_dispatcher = FrameDispatcher(
            charger=second_charger,
            version=ProtocolVersion.OCPP_16,
            pending_calls=PendingCalls(),
            handler_resolver=Inbound16Actions(second_charger).resolve,
        )

        async_to_sync(first_dispatcher.dispatch)(frame)
        async_to_sync(second_dispatcher.dispatch)(frame)

        self.assertEqual(
            InboundProtocolRequest.objects.filter(unique_id="shared-call").count(),
            2,
        )
        self.assertEqual(
            NotificationRecord.objects.filter(action="DataTransfer").count(),
            2,
        )

import pytest
from asgiref.sync import async_to_sync

from apps.ocpp.models import InboundProtocolRequest, NotificationRecord
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallResult
from apps.ocpp.protocol.v16.inbound import InboundActions as Inbound16Actions
from apps.ocpp.protocol.v201.inbound import InboundActions as Inbound201Actions
from apps.ocpp.transport.dispatch import FrameDispatcher
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


def dispatcher(selected, version: ProtocolVersion) -> FrameDispatcher:
    actions = (
        Inbound16Actions(selected)
        if version == ProtocolVersion.OCPP_16
        else Inbound201Actions(selected)
    )
    return FrameDispatcher(
        charger=selected,
        version=version,
        pending_calls=PendingCalls(),
        handler_resolver=actions.resolve,
    )


def test_ocpp16_replay_survives_fresh_dispatcher_instance() -> None:
    selected = charger("restart-16")
    frame = Call(
        unique_id="transfer-1",
        action="DataTransfer",
        payload={"vendorId": "ACME", "data": "payload"},
    )

    first = async_to_sync(dispatcher(selected, ProtocolVersion.OCPP_16).dispatch)(frame)
    replayed = async_to_sync(
        dispatcher(selected, ProtocolVersion.OCPP_16).dispatch
    )(frame)

    assert first == CallResult(
        unique_id="transfer-1",
        payload={"status": "Accepted"},
    )
    assert replayed == first
    assert (
        NotificationRecord.objects.filter(
            charger=selected,
            action="DataTransfer",
        ).count()
        == 1
    )
    request = InboundProtocolRequest.objects.get(
        charger=selected,
        unique_id="transfer-1",
    )
    assert request.status == InboundProtocolRequest.Status.COMPLETED


def test_ocpp201_replay_survives_fresh_dispatcher_instance() -> None:
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

    first = async_to_sync(dispatcher(selected, ProtocolVersion.OCPP_201).dispatch)(frame)
    replayed = async_to_sync(
        dispatcher(selected, ProtocolVersion.OCPP_201).dispatch
    )(frame)

    assert first == CallResult(unique_id="notify-1", payload={})
    assert replayed == first
    assert (
        NotificationRecord.objects.filter(
            charger=selected,
            action="NotifyEvent",
        ).count()
        == 1
    )
    request = InboundProtocolRequest.objects.get(
        charger=selected,
        unique_id="notify-1",
    )
    assert request.version == ProtocolVersion.OCPP_201.value


def test_same_call_id_isolated_between_chargers_after_restart() -> None:
    first_charger = charger("restart-a")
    second_charger = charger("restart-b")
    frame = Call(
        unique_id="shared-call",
        action="DataTransfer",
        payload={"vendorId": "ACME"},
    )

    async_to_sync(dispatcher(first_charger, ProtocolVersion.OCPP_16).dispatch)(frame)
    async_to_sync(dispatcher(second_charger, ProtocolVersion.OCPP_16).dispatch)(frame)

    assert InboundProtocolRequest.objects.filter(unique_id="shared-call").count() == 2
    assert NotificationRecord.objects.filter(action="DataTransfer").count() == 2

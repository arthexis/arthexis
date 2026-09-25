from asgiref.sync import async_to_sync
import pytest
from django.test import override_settings

from apps.cards.models import CardCredential
from apps.ocpp.models import Charger, Connector, OcppTransaction
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallError, CallResult
from apps.ocpp.protocol.v16.inbound import InboundActions
from apps.ocpp.transport.dispatch import FrameDispatcher
from tests.apps.ocpp.builders import charger, connection

pytestmark = pytest.mark.django_db


@pytest.fixture
def inbound_context():
    selected = charger("charger-1")
    card = CardCredential.objects.create(
        external_id="card-1",
        ocpp_id_tag="card-tag",
    )
    restricted = charger(
        "charger-restricted",
        authorization_mode=Charger.AuthorizationMode.RESTRICTED,
    )
    dispatcher = FrameDispatcher(
        charger=selected,
        version=ProtocolVersion.OCPP_16,
        pending_calls=PendingCalls(),
        handler_resolver=InboundActions(selected).resolve,
    )
    return selected, card, restricted, dispatcher


def response(dispatcher, unique_id: str, action: str, payload: dict[str, object]):
    result = async_to_sync(dispatcher.dispatch)(
        Call(unique_id=unique_id, action=action, payload=payload)
    )
    assert result is not None
    return result


def call(dispatcher, unique_id: str, action: str, payload: dict[str, object]) -> list[object]:
    result = response(dispatcher, unique_id, action, payload)
    assert isinstance(result, CallResult)
    return result.to_wire()


def test_every_inbound_action_returns_its_expected_call_result(inbound_context) -> None:
    selected, card, _, dispatcher = inbound_context

    boot = call(
        dispatcher,
        "boot-1",
        "BootNotification",
        {"chargePointModel": "Model", "chargePointVendor": "ACME"},
    )
    assert boot[:2] == [3, "boot-1"]
    assert boot[2]["status"] == "Accepted"
    assert boot[2]["interval"] == 300
    assert "currentTime" in boot[2]

    heartbeat = call(dispatcher, "heartbeat-1", "Heartbeat", {})
    assert heartbeat[:2] == [3, "heartbeat-1"]
    assert set(heartbeat[2]) == {"currentTime"}

    assert call(
        dispatcher,
        "authorize-1",
        "Authorize",
        {"idTag": card.ocpp_id_tag},
    ) == [3, "authorize-1", {"idTagInfo": {"status": "Accepted"}}]
    assert call(
        dispatcher,
        "status-1",
        "StatusNotification",
        {"connectorId": 1, "status": "Preparing"},
    ) == [3, "status-1", {}]

    started = call(
        dispatcher,
        "start-1",
        "StartTransaction",
        {
            "connectorId": 1,
            "idTag": card.ocpp_id_tag,
            "meterStart": 100,
            "timestamp": "2026-01-01T00:00:00Z",
        },
    )
    assert started[:2] == [3, "start-1"]
    assert started[2]["idTagInfo"] == {"status": "Accepted"}
    transaction_id = started[2]["transactionId"]
    assert isinstance(transaction_id, int)

    assert call(
        dispatcher,
        "meter-1",
        "MeterValues",
        {
            "transactionId": transaction_id,
            "meterValue": [
                {
                    "sampledValue": [{"value": "125"}],
                    "timestamp": "2026-01-01T00:05:00Z",
                }
            ],
        },
    ) == [3, "meter-1", {}]
    assert call(
        dispatcher,
        "stop-1",
        "StopTransaction",
        {
            "meterStop": 150,
            "timestamp": "2026-01-01T00:10:00Z",
            "transactionId": transaction_id,
        },
    ) == [3, "stop-1", {"idTagInfo": {"status": "Accepted"}}]
    assert call(
        dispatcher,
        "transfer-1",
        "DataTransfer",
        {"vendorId": "ACME"},
    ) == [3, "transfer-1", {"status": "Accepted"}]
    assert call(
        dispatcher,
        "diagnostics-1",
        "DiagnosticsStatusNotification",
        {"status": "Uploaded"},
    ) == [3, "diagnostics-1", {}]
    assert call(
        dispatcher,
        "firmware-1",
        "FirmwareStatusNotification",
        {"status": "Downloaded"},
    ) == [3, "firmware-1", {}]

    assert Connector.objects.get(charger=selected).status == "Preparing"
    assert OcppTransaction.objects.get(pk=transaction_id).stopped_at is not None
    assert selected.ocpp_notifications.get().action == "DataTransfer"
    assert selected.operational_statuses.count() == 2


def test_authorize_and_start_follow_the_configured_policy(inbound_context) -> None:
    selected, _, restricted, _ = inbound_context
    open_actions = InboundActions(selected)._handlers
    restricted_actions = InboundActions(restricted)._handlers

    assert async_to_sync(open_actions["Authorize"])({"idTag": "guest-card"}) == {
        "idTagInfo": {"status": "Accepted"}
    }
    started = async_to_sync(open_actions["StartTransaction"])(
        {"connectorId": 1, "idTag": "guest-card"}
    )
    assert started["idTagInfo"] == {"status": "Accepted"}
    assert async_to_sync(restricted_actions["Authorize"])({"idTag": "guest-card"}) == {
        "idTagInfo": {"status": "Invalid"}
    }
    assert async_to_sync(restricted_actions["StartTransaction"])(
        {"connectorId": 1, "idTag": "guest-card"}
    ) == {"idTagInfo": {"status": "Invalid"}}


def test_invalid_and_unknown_calls_return_correlated_call_errors(inbound_context) -> None:
    _, _, _, dispatcher = inbound_context
    invalid = response(dispatcher, "invalid-boot", "BootNotification", {})
    assert invalid.to_wire() == [
        4,
        "invalid-boot",
        "FormationViolation",
        "Invalid payload.",
        {},
    ]
    unknown = response(dispatcher, "unknown-1", "Unsupported", {})
    assert unknown.to_wire() == [
        4,
        "unknown-1",
        "NotSupported",
        "Unsupported action: Unsupported",
        {},
    ]
    assert isinstance(invalid, CallError)
    assert isinstance(unknown, CallError)


@override_settings(OCPP_HEARTBEAT_INTERVAL_SECONDS=120)
def test_boot_notification_persists_advertised_heartbeat_interval() -> None:
    selected = charger("charger-16-heartbeat")
    live = connection(selected, channel_name="channel-1")
    handler = InboundActions(selected)._handlers["BootNotification"]

    result = async_to_sync(handler)(
        {"chargePointModel": "Model", "chargePointVendor": "ACME"}
    )

    assert result["interval"] == 120
    live.refresh_from_db()
    assert live.heartbeat_interval_seconds == 120
    assert live.lease_expires_at > live.last_seen_at

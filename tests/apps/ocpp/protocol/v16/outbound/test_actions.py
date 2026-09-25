import pytest
from asgiref.sync import async_to_sync

from apps.ocpp.models import ProtocolOperation
from apps.ocpp.protocol.v16.outbound import VALIDATORS, validate_outbound
from apps.ocpp.transport.operations import active_connections, emit_v16_operation
from tests.apps.ocpp.builders import charger
from tests.apps.ocpp.fakes import RecordingSender

pytestmark = pytest.mark.django_db

VALID_PAYLOADS = {
    "CancelReservation": {"reservationId": 1},
    "ChangeAvailability": {"connectorId": 1, "type": "Operative"},
    "ChangeConfiguration": {"key": "HeartbeatInterval", "value": "300"},
    "ClearChargingProfile": {},
    "DataTransfer": {"vendorId": "ACME"},
    "GetCompositeSchedule": {"connectorId": 1, "duration": 60},
    "GetConfiguration": {},
    "GetDiagnostics": {"location": "https://example.test/diagnostics"},
    "GetLocalListVersion": {},
    "RemoteStartTransaction": {"idTag": "card-1"},
    "RemoteStopTransaction": {"transactionId": 1},
    "Reset": {"type": "Soft"},
    "ReserveNow": {
        "connectorId": 1,
        "expiryDate": "2026-01-01T00:00:00Z",
        "idTag": "card-1",
        "reservationId": 1,
    },
    "SendLocalList": {"listVersion": 1, "updateType": "Full"},
    "SetChargingProfile": {"connectorId": 1, "csChargingProfiles": {}},
    "TriggerMessage": {"requestedMessage": "StatusNotification"},
    "UnlockConnector": {"connectorId": 1},
    "UpdateFirmware": {
        "location": "https://example.test/firmware",
        "retrieveDate": "2026-01-01T00:00:00Z",
    },
}


def test_explicit_outbound_operation_records_its_correlated_result() -> None:
    selected = charger("charger-1")
    sender = RecordingSender()
    active_connections.register(selected, sender)
    try:
        operation = async_to_sync(emit_v16_operation)(
            charger=selected,
            action="ChangeConfiguration",
            payload={"key": "HeartbeatInterval", "value": "300"},
        )
    finally:
        active_connections.unregister(selected)

    assert operation.status == ProtocolOperation.Status.COMPLETED
    assert sender.calls[0]["unique_id"] == str(operation.unique_id)
    assert operation.response_payload == {"status": "Accepted"}


@pytest.mark.parametrize(
    ("action", "payload"),
    list(VALID_PAYLOADS.items()),
)
def test_outbound_payloads_accept_their_action_contract(
    action: str,
    payload: dict[str, object],
) -> None:
    assert validate_outbound(action, payload) == payload


def test_outbound_validator_registry_matches_payload_matrix() -> None:
    assert set(VALID_PAYLOADS) == set(VALIDATORS)


@pytest.mark.parametrize(
    ("action", "payload"),
    [
        ("ChangeConfiguration", {"key": "HeartbeatInterval"}),
        ("Unknown", {}),
    ],
)
def test_outbound_payloads_reject_invalid_contracts(
    action: str,
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        validate_outbound(action, payload)

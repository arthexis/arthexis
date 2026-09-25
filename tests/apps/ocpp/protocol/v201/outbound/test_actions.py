import pytest
from asgiref.sync import async_to_sync

from apps.ocpp.models import ProtocolOperation
from apps.ocpp.protocol.v201.outbound import VALIDATORS, validate_outbound
from apps.ocpp.transport.operations import active_connections, emit_v201_operation
from tests.apps.ocpp.builders import charger
from tests.apps.ocpp.fakes import RecordingSender

pytestmark = pytest.mark.django_db

VALID_PAYLOADS = {
    "CancelReservation": {"reservationId": 1},
    "CertificateSigned": {"certificateChain": "chain"},
    "ChangeAvailability": {"operationalStatus": "Operative"},
    "ClearChargingProfile": {},
    "ClearDisplayMessage": {"id": 1},
    "ClearVariableMonitoring": {"id": [1]},
    "CustomerInformation": {"reportBase": "FullInventory", "requestId": 1},
    "DataTransfer": {"vendorId": "ACME"},
    "DeleteCertificate": {"certificateHashData": {}},
    "GetBaseReport": {"reportBase": "FullInventory", "requestId": 1},
    "GetCompositeSchedule": {"duration": 60, "evseId": 1},
    "GetDisplayMessages": {"requestId": 1},
    "GetInstalledCertificateIds": {"certificateType": ["V2GRootCertificate"]},
    "GetLocalListVersion": {},
    "GetLog": {
        "logType": "DiagnosticsLog",
        "remoteLocation": "https://x",
        "requestId": 1,
    },
    "GetReport": {"componentVariable": [], "requestId": 1},
    "GetVariables": {"getVariableData": []},
    "InstallCertificate": {
        "certificate": "certificate",
        "certificateType": "V2GRootCertificate",
    },
    "PublishFirmware": {"location": "https://x", "requestId": 1},
    "RequestStartTransaction": {"idToken": {}, "remoteStartId": 1},
    "RequestStopTransaction": {"transactionId": "transaction-1"},
    "ReserveNow": {"expiryDate": "2026-01-01T00:00:00Z", "id": 1, "idToken": {}},
    "Reset": {"type": "OnIdle"},
    "SendLocalList": {"updateType": "Full", "version": 1},
    "SetChargingProfile": {"chargingProfile": {}, "evseId": 1},
    "SetDisplayMessage": {"message": {}},
    "SetMonitoringBase": {"type": "All"},
    "SetMonitoringLevel": {"severity": 1},
    "SetVariableMonitoring": {"setVariableMonitoring": []},
    "SetVariables": {"setVariableData": []},
    "TriggerMessage": {"requestedMessage": "StatusNotification"},
    "UnlockConnector": {"connectorId": 1, "evseId": 1},
    "UpdateFirmware": {"firmware": {}, "requestId": 1},
}


def test_explicit_outbound_operation_records_its_correlated_result() -> None:
    selected = charger("charger-201")
    sender = RecordingSender()
    active_connections.register(selected, sender)
    try:
        operation = async_to_sync(emit_v201_operation)(
            charger=selected,
            action="GetVariables",
            payload={"getVariableData": []},
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
        ("GetVariables", {}),
        ("Unknown", {}),
    ],
)
def test_outbound_payloads_reject_invalid_contracts(
    action: str,
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        validate_outbound(action, payload)

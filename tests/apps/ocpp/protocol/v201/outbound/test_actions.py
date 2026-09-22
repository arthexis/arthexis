from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.ocpp.models import ProtocolOperation
from apps.ocpp.protocol.v201.outbound import VALIDATORS, validate_outbound
from apps.ocpp.transport.operations import active_connections, emit_v201_operation
from tests.apps.ocpp.builders import charger
from tests.apps.ocpp.fakes import RecordingSender

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


class Ocpp201OutboundTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("charger-201")

    def tearDown(self) -> None:
        active_connections.unregister(self.charger)

    def test_explicit_outbound_operation_records_its_correlated_result(self) -> None:
        sender = RecordingSender()
        active_connections.register(self.charger, sender)

        operation = async_to_sync(emit_v201_operation)(
            charger=self.charger,
            action="GetVariables",
            payload={"getVariableData": []},
        )

        self.assertEqual(operation.status, ProtocolOperation.Status.COMPLETED)
        self.assertEqual(sender.calls[0]["unique_id"], str(operation.unique_id))
        self.assertEqual(operation.response_payload, {"status": "Accepted"})

    def test_outbound_payloads_require_their_action_contract(self) -> None:
        self.assertEqual(set(VALID_PAYLOADS), set(VALIDATORS))
        for action, payload in VALID_PAYLOADS.items():
            self.assertEqual(validate_outbound(action, payload), payload)
        with self.assertRaises(ValueError):
            validate_outbound("GetVariables", {})
        with self.assertRaises(ValueError):
            validate_outbound("Unknown", {})

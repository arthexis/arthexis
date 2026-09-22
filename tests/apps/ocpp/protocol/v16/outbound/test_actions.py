from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.ocpp.models import ProtocolOperation
from apps.ocpp.protocol.v16.outbound import VALIDATORS, validate_outbound
from apps.ocpp.transport.operations import active_connections, emit_v16_operation
from tests.apps.ocpp.builders import charger
from tests.apps.ocpp.fakes import RecordingSender

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


class Ocpp16OutboundTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("charger-1")

    def tearDown(self) -> None:
        active_connections.unregister(self.charger)

    def test_explicit_outbound_operation_records_its_correlated_result(self) -> None:
        sender = RecordingSender()
        active_connections.register(self.charger, sender)

        operation = async_to_sync(emit_v16_operation)(
            charger=self.charger,
            action="ChangeConfiguration",
            payload={"key": "HeartbeatInterval", "value": "300"},
        )

        self.assertEqual(operation.status, ProtocolOperation.Status.COMPLETED)
        self.assertEqual(sender.calls[0]["unique_id"], str(operation.unique_id))
        self.assertEqual(operation.response_payload, {"status": "Accepted"})

    def test_outbound_payloads_require_their_action_contract(self) -> None:
        self.assertEqual(set(VALID_PAYLOADS), set(VALIDATORS))
        for action, payload in VALID_PAYLOADS.items():
            self.assertEqual(validate_outbound(action, payload), payload)
        with self.assertRaises(ValueError):
            validate_outbound("ChangeConfiguration", {"key": "HeartbeatInterval"})
        with self.assertRaises(ValueError):
            validate_outbound("Unknown", {})

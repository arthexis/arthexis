from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.ocpp.models import ProtocolOperation
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from apps.ocpp.protocol.registry import ALL_ACTIONS
from apps.ocpp.protocol.v201.inbound import InboundActions
from apps.ocpp.protocol.v201.outbound import VALIDATORS, validate_outbound
from apps.ocpp.transport.operations import active_connections, emit_v201_operation
from tests.ocpp.builders import charger
from tests.ocpp.fakes import RecordingSender

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

INBOUND_PAYLOADS = {
    "Authorize": {"idToken": {"idToken": "card-1"}},
    "BootNotification": {"chargingStation": {"model": "Model", "vendorName": "ACME"}},
    "ClearedChargingLimit": {},
    "CostUpdated": {},
    "DataTransfer": {"vendorId": "ACME"},
    "FirmwareStatusNotification": {"status": "Downloaded"},
    "Get15118EVCertificate": {},
    "GetCertificateStatus": {},
    "Heartbeat": {},
    "LogStatusNotification": {"status": "Uploaded"},
    "MeterValues": {
        "meterValue": [{"sampledValue": [{"value": "10"}]}],
        "transactionInfo": {"transactionId": "transaction-1"},
    },
    "NotifyChargingLimit": {},
    "NotifyCustomerInformation": {},
    "NotifyDisplayMessages": {},
    "NotifyEVChargingNeeds": {},
    "NotifyEVChargingSchedule": {},
    "NotifyEvent": {},
    "NotifyMonitoringReport": {},
    "NotifyReport": {},
    "PublishFirmwareStatusNotification": {"status": "Downloaded"},
    "ReportChargingProfiles": {},
    "ReservationStatusUpdate": {},
    "SecurityEventNotification": {},
    "SignCertificate": {"csr": "request"},
    "StatusNotification": {
        "connectorId": 1,
        "connectorStatus": "Available",
        "evseId": 1,
    },
    "TransactionEvent": {
        "eventType": "Started",
        "evse": {"connectorId": 1, "id": 1},
        "idToken": {"idToken": "card-1"},
        "timestamp": "2026-01-01T00:00:00Z",
        "transactionInfo": {"transactionId": "transaction-1"},
    },
}


class Ocpp201ActionTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("charger-201")

    def tearDown(self) -> None:
        active_connections.unregister(self.charger)

    def test_every_frozen_action_has_a_handler_or_outbound_validator(self) -> None:
        expected_inbound = {
            contract.action
            for contract in ALL_ACTIONS
            if contract.version == ProtocolVersion.OCPP_201
            and contract.direction == Direction.CHARGE_POINT_TO_CSMS
        }
        expected_outbound = {
            contract.action
            for contract in ALL_ACTIONS
            if contract.version == ProtocolVersion.OCPP_201
            and contract.direction == Direction.CSMS_TO_CHARGE_POINT
        }

        self.assertEqual(set(InboundActions(self.charger)._handlers), expected_inbound)
        self.assertEqual(set(VALIDATORS), expected_outbound)

    def test_every_inbound_action_accepts_its_retained_payload(self) -> None:
        handlers = InboundActions(self.charger)._handlers
        for action in ("TransactionEvent", "MeterValues"):
            async_to_sync(handlers[action])(INBOUND_PAYLOADS[action])
        for action, payload in INBOUND_PAYLOADS.items():
            if action not in {"MeterValues", "TransactionEvent"}:
                async_to_sync(handlers[action])(payload)

    def test_standalone_meter_values_are_retained_without_transaction(self) -> None:
        response = async_to_sync(InboundActions(self.charger)._handlers["MeterValues"])(
            {
                "evseId": 1,
                "meterValue": [
                    {
                        "timestamp": "2026-01-01T00:00:00Z",
                        "sampledValue": [{"value": 10}],
                    }
                ],
            }
        )

        self.assertEqual(response, {})
        notification = self.charger.ocpp_notifications.get(action="MeterValues")
        self.assertEqual(notification.payload["evseId"], 1)

    def test_conformance_sensitive_inbound_responses_are_complete(self) -> None:
        handlers = InboundActions(self.charger)._handlers

        self.assertEqual(
            async_to_sync(handlers["Get15118EVCertificate"])({}),
            {"status": "Failed", "exiResponse": ""},
        )
        self.assertEqual(
            async_to_sync(handlers["NotifyEVChargingNeeds"])({}),
            {"status": "Accepted"},
        )
        self.assertEqual(
            async_to_sync(handlers["NotifyEVChargingSchedule"])({}),
            {"status": "Accepted"},
        )

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

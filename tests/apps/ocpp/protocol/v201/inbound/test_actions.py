from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.ocpp.protocol.v201.inbound import InboundActions
from tests.apps.ocpp.builders import charger

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


class Ocpp201InboundTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("charger-201")

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

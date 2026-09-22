from asgiref.sync import async_to_sync
from django.test import TestCase, override_settings

from apps.ocpp.models import Charger, OcppTransaction
from apps.ocpp.protocol.v201.inbound import InboundActions
from tests.apps.ocpp.builders import charger, connection

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
        self.restricted_charger = charger(
            "charger-restricted",
            authorization_mode=Charger.AuthorizationMode.RESTRICTED,
        )

    def test_every_inbound_action_accepts_its_retained_payload(self) -> None:
        handlers = InboundActions(self.charger)._handlers
        for action in ("TransactionEvent", "MeterValues"):
            async_to_sync(handlers[action])(INBOUND_PAYLOADS[action])
        for action, payload in INBOUND_PAYLOADS.items():
            if action not in {"MeterValues", "TransactionEvent"}:
                async_to_sync(handlers[action])(payload)

    def test_authorize_and_started_transaction_follow_the_policy(self) -> None:
        open_actions = InboundActions(self.charger)._handlers
        restricted_actions = InboundActions(self.restricted_charger)._handlers
        payload = {
            "eventType": "Started",
            "idToken": {"idToken": "guest-card"},
            "transactionInfo": {"transactionId": "transaction-open"},
        }

        self.assertEqual(
            async_to_sync(open_actions["Authorize"])(
                {"idToken": {"idToken": "guest-card"}}
            ),
            {"idTokenInfo": {"status": "Accepted"}},
        )
        self.assertEqual(
            async_to_sync(open_actions["TransactionEvent"])(payload),
            {"idTokenInfo": {"status": "Accepted"}},
        )
        self.assertTrue(
            OcppTransaction.objects.filter(
                charger=self.charger,
                remote_id="transaction-open",
            ).exists()
        )
        self.assertEqual(
            async_to_sync(restricted_actions["Authorize"])(
                {"idToken": {"idToken": "guest-card"}}
            ),
            {"idTokenInfo": {"status": "Invalid"}},
        )
        self.assertEqual(
            async_to_sync(restricted_actions["TransactionEvent"])(
                {
                    **payload,
                    "transactionInfo": {"transactionId": "transaction-restricted"},
                }
            ),
            {"idTokenInfo": {"status": "Invalid"}},
        )
        self.assertFalse(
            OcppTransaction.objects.filter(
                charger=self.restricted_charger,
                remote_id="transaction-restricted",
            ).exists()
        )

    def test_started_transaction_requires_a_nonempty_identifier(self) -> None:
        actions = InboundActions(self.charger)._handlers

        with self.assertRaises(ValueError):
            async_to_sync(actions["TransactionEvent"])(
                {
                    "eventType": "Started",
                    "idToken": {"idToken": ""},
                    "transactionInfo": {"transactionId": "transaction-empty"},
                }
            )

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
        batch = self.charger.meter_reading_batches.get()
        self.assertEqual(batch.evse_id, 1)
        self.assertEqual(batch.payload["evseId"], 1)
        self.assertEqual(
            batch.reported_at.isoformat(),
            "2026-01-01T00:00:00+00:00",
        )

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


class Ocpp201HeartbeatLeaseTests(TestCase):
    @override_settings(OCPP_HEARTBEAT_INTERVAL_SECONDS=90)
    def test_boot_notification_persists_advertised_heartbeat_interval(self) -> None:
        selected = charger("charger-201-heartbeat")
        live = connection(selected, channel_name="channel-1")
        handler = InboundActions(selected)._handlers["BootNotification"]

        response = async_to_sync(handler)(
            {"chargingStation": {"model": "Model", "vendorName": "ACME"}}
        )

        self.assertEqual(response["interval"], 90)
        live.refresh_from_db()
        self.assertEqual(live.heartbeat_interval_seconds, 90)
        self.assertGreater(live.lease_expires_at, live.last_seen_at)

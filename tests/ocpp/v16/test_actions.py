from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.cards.models import CardCredential
from apps.ocpp.models import Connector, OcppTransaction, ProtocolOperation
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallError, CallResult
from apps.ocpp.protocol.registry import ALL_ACTIONS
from apps.ocpp.protocol.v16.inbound import InboundActions
from apps.ocpp.protocol.v16.outbound import VALIDATORS, validate_outbound
from apps.ocpp.transport.dispatch import FrameDispatcher
from apps.ocpp.transport.operations import active_connections, emit_v16_operation
from tests.ocpp.builders import charger
from tests.ocpp.fakes import RecordingSender

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


class Ocpp16ActionTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("charger-1")
        self.card = CardCredential.objects.create(
            external_id="card-1",
            ocpp_id_tag="card-tag",
        )
        self.dispatcher = FrameDispatcher(
            version=ProtocolVersion.OCPP_16,
            pending_calls=PendingCalls(),
            handler_resolver=InboundActions(self.charger).resolve,
        )

    def tearDown(self) -> None:
        active_connections.unregister(self.charger)

    def test_every_frozen_v16_action_has_a_handler_or_outbound_validator(self) -> None:
        inbound_actions = set(InboundActions(self.charger)._handlers)
        expected_inbound = {
            contract.action
            for contract in ALL_ACTIONS
            if contract.version == ProtocolVersion.OCPP_16
            and contract.direction == Direction.CHARGE_POINT_TO_CSMS
        }
        expected_outbound = {
            contract.action
            for contract in ALL_ACTIONS
            if contract.version == ProtocolVersion.OCPP_16
            and contract.direction == Direction.CSMS_TO_CHARGE_POINT
        }

        self.assertEqual(inbound_actions, expected_inbound)
        self.assertEqual(set(VALIDATORS), expected_outbound)

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

    def test_every_inbound_action_returns_its_expected_call_result(self) -> None:
        boot = self._call("boot-1", "BootNotification", self._boot_payload())
        self.assertEqual(boot[:2], [3, "boot-1"])
        self.assertEqual(boot[2]["status"], "Accepted")
        self.assertEqual(boot[2]["interval"], 300)
        self.assertIn("currentTime", boot[2])

        heartbeat = self._call("heartbeat-1", "Heartbeat", {})
        self.assertEqual(heartbeat[:2], [3, "heartbeat-1"])
        self.assertEqual(set(heartbeat[2]), {"currentTime"})

        self.assertEqual(
            self._call("authorize-1", "Authorize", {"idTag": self.card.ocpp_id_tag}),
            [3, "authorize-1", {"idTagInfo": {"status": "Accepted"}}],
        )
        self.assertEqual(
            self._call("status-1", "StatusNotification", self._status_payload()),
            [3, "status-1", {}],
        )

        started = self._call("start-1", "StartTransaction", self._start_payload())
        self.assertEqual(started[:2], [3, "start-1"])
        self.assertEqual(started[2]["idTagInfo"], {"status": "Accepted"})
        transaction_id = started[2]["transactionId"]
        self.assertIsInstance(transaction_id, int)

        self.assertEqual(
            self._call("meter-1", "MeterValues", self._meter_payload(transaction_id)),
            [3, "meter-1", {}],
        )
        self.assertEqual(
            self._call("stop-1", "StopTransaction", self._stop_payload(transaction_id)),
            [3, "stop-1", {"idTagInfo": {"status": "Accepted"}}],
        )
        self.assertEqual(
            self._call("transfer-1", "DataTransfer", {"vendorId": "ACME"}),
            [3, "transfer-1", {"status": "Accepted"}],
        )
        self.assertEqual(
            self._call(
                "diagnostics-1",
                "DiagnosticsStatusNotification",
                {"status": "Uploaded"},
            ),
            [3, "diagnostics-1", {}],
        )
        self.assertEqual(
            self._call(
                "firmware-1",
                "FirmwareStatusNotification",
                {"status": "Downloaded"},
            ),
            [3, "firmware-1", {}],
        )

        self.assertEqual(
            Connector.objects.get(charger=self.charger).status, "Preparing"
        )
        self.assertTrue(
            OcppTransaction.objects.get(pk=transaction_id).stopped_at is not None
        )
        self.assertEqual(
            self.charger.ocpp_notifications.get().action,
            "DataTransfer",
        )
        self.assertEqual(self.charger.operational_statuses.count(), 2)

    def test_invalid_and_unknown_calls_return_correlated_call_errors(self) -> None:
        invalid = self._response("invalid-boot", "BootNotification", {})
        self.assertEqual(
            invalid.to_wire(),
            [4, "invalid-boot", "FormationViolation", "Invalid payload.", {}],
        )
        unknown = self._response("unknown-1", "Unsupported", {})
        self.assertEqual(
            unknown.to_wire(),
            [4, "unknown-1", "NotSupported", "Unsupported action: Unsupported", {}],
        )

    def _call(
        self, unique_id: str, action: str, payload: dict[str, object]
    ) -> list[object]:
        response = self._response(unique_id, action, payload)
        self.assertIsInstance(response, CallResult)
        return response.to_wire()

    def _response(
        self, unique_id: str, action: str, payload: dict[str, object]
    ) -> CallResult | CallError:
        response = async_to_sync(self.dispatcher.dispatch)(
            Call(unique_id=unique_id, action=action, payload=payload)
        )
        self.assertIsNotNone(response)
        return response

    @staticmethod
    def _boot_payload() -> dict[str, object]:
        return {"chargePointModel": "Model", "chargePointVendor": "ACME"}

    @staticmethod
    def _status_payload() -> dict[str, object]:
        return {"connectorId": 1, "status": "Preparing"}

    def _start_payload(self) -> dict[str, object]:
        return {
            "connectorId": 1,
            "idTag": self.card.ocpp_id_tag,
            "meterStart": 100,
            "timestamp": "2026-01-01T00:00:00Z",
        }

    @staticmethod
    def _meter_payload(transaction_id: int) -> dict[str, object]:
        return {
            "transactionId": transaction_id,
            "meterValue": [
                {
                    "sampledValue": [{"value": "125"}],
                    "timestamp": "2026-01-01T00:05:00Z",
                }
            ],
        }

    @staticmethod
    def _stop_payload(transaction_id: int) -> dict[str, object]:
        return {
            "meterStop": 150,
            "timestamp": "2026-01-01T00:10:00Z",
            "transactionId": transaction_id,
        }

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.cards.models import CardCredential
from apps.ocpp.models import Connector, OcppTransaction
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallError, CallResult
from apps.ocpp.protocol.v16.inbound import InboundActions
from apps.ocpp.transport.dispatch import FrameDispatcher
from tests.apps.ocpp.builders import charger


class Ocpp16InboundTests(TestCase):
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

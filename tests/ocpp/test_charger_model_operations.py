from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.ocpp.models import Charger
from tests.ocpp.builders import charger, station_model, transaction


class ChargerModelOperationTests(TestCase):
    def setUp(self) -> None:
        self.v16 = station_model(model="One", protocol="ocpp1.6")
        self.v201 = station_model(model="Two", protocol="ocpp2.0.1")
        self.charger = charger("charger-1", station=self.v16)

    @patch(
        "apps.ocpp.transport.operations.request_explicit_operation",
        new_callable=AsyncMock,
    )
    def test_reset_uses_the_selected_charger_protocol(self, request) -> None:
        request.return_value = SimpleNamespace(action="Reset", unique_id="operation-1")

        operation = async_to_sync(Charger.reset)(self.charger)

        self.assertEqual(operation.action, "Reset")
        self.assertEqual(request.await_args.kwargs["charger"], self.charger)
        self.assertEqual(request.await_args.kwargs["payload"], {"type": "Soft"})

    @patch(
        "apps.ocpp.transport.operations.request_explicit_operation",
        new_callable=AsyncMock,
    )
    def test_start_uses_the_selected_charger_protocol(self, request) -> None:
        request.return_value = SimpleNamespace(
            action="RequestStartTransaction", unique_id="operation-2"
        )
        selected = charger("charger-201", station=self.v201)

        async_to_sync(Charger.start)(selected, id_token="member-2", evse=3)

        payload = request.await_args.kwargs["payload"]
        self.assertEqual(request.await_args.kwargs["action"], "RequestStartTransaction")
        self.assertEqual(payload["idToken"], {"idToken": "member-2"})
        self.assertEqual(payload["evseId"], 3)
        self.assertIsInstance(payload["remoteStartId"], int)

    @patch(
        "apps.ocpp.transport.operations.request_explicit_operation",
        new_callable=AsyncMock,
    )
    def test_stop_derives_the_sole_active_transaction(self, request) -> None:
        request.return_value = SimpleNamespace(
            action="RemoteStopTransaction", unique_id="operation-3"
        )
        active = transaction(
            self.charger,
            "transaction-active",
            started_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

        async_to_sync(Charger.stop)(self.charger)

        self.assertEqual(request.await_args.kwargs["action"], "RemoteStopTransaction")
        self.assertEqual(
            request.await_args.kwargs["payload"], {"transactionId": active.remote_id}
        )

    def test_start_and_stop_reject_invalid_model_state(self) -> None:
        with self.assertRaisesMessage(ValueError, "non-empty id_token"):
            async_to_sync(Charger.start)(self.charger, id_token="")
        with self.assertRaisesMessage(ValueError, "no active transaction"):
            async_to_sync(Charger.stop)(self.charger)

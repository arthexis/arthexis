import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.admin import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase

from ocpp.admin import ChargerAdmin
from ocpp.models import Charger
from nodes.models import Node


class _DummyWebSocket:
    def __init__(self):
        self.sent: list[str] = []

    async def send(self, message: str) -> None:  # pragma: no cover - exercised via async wrapper
        self.sent.append(message)


class ChargerAdminUnlockConnectorTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin_site = AdminSite()
        self.admin = ChargerAdmin(Charger, self.admin_site)
        User = get_user_model()
        self.user = User.objects.create_superuser("admin", "admin@example.com", "password")

    def _build_request(self):
        request = self.factory.post("/admin/ocpp/charger/")
        request.user = self.user
        # Messages framework requires a session for FallbackStorage.
        request.session = self.client.session
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def test_unlock_connector_local_success(self):
        local_node = Node.objects.create(hostname="local-node", mac_address="00:00:00:00:00:01")
        with patch("ocpp.models.Node.get_local", return_value=local_node):
            charger = Charger.objects.create(charger_id="CP-LOCAL", connector_id=2)

        request = self._build_request()
        websocket = _DummyWebSocket()

        with (
            patch("ocpp.models.Node.get_local", return_value=local_node),
            patch("ocpp.store.get_connection", return_value=websocket),
            patch("ocpp.store.add_log") as mock_add_log,
            patch("ocpp.store.register_pending_call") as mock_register,
            patch("ocpp.store.schedule_call_timeout") as mock_timeout,
            patch("uuid.uuid4", return_value=SimpleNamespace(hex="uuid-123")),
        ):
            self.admin.unlock_connector(request, Charger.objects.filter(pk=charger.pk))

        self.assertEqual(len(websocket.sent), 1)
        frame = json.loads(websocket.sent[0])
        self.assertEqual(frame[2], "UnlockConnector")
        self.assertEqual(frame[3], {"connectorId": 2})

        mock_add_log.assert_called_once()
        mock_register.assert_called_once()
        self.assertEqual(mock_register.call_args.args[0], "uuid-123")
        metadata = mock_register.call_args.args[1]
        self.assertEqual(metadata["action"], "UnlockConnector")
        self.assertEqual(metadata["charger_id"], "CP-LOCAL")
        self.assertEqual(metadata["connector_id"], 2)
        mock_timeout.assert_called_once()
        timeout_kwargs = mock_timeout.call_args.kwargs
        self.assertEqual(timeout_kwargs.get("action"), "UnlockConnector")

        messages = [message.message for message in request._messages]
        self.assertIn("Sent UnlockConnector to 1 charger(s)", messages)

    def test_unlock_connector_rejects_aggregate_entries(self):
        local_node = Node.objects.create(hostname="local-node", mac_address="00:00:00:00:00:02")
        with patch("ocpp.models.Node.get_local", return_value=local_node):
            charger = Charger.objects.create(charger_id="CP-AGG")

        request = self._build_request()
        with (
            patch("ocpp.models.Node.get_local", return_value=local_node),
            patch("ocpp.store.get_connection") as mock_get_connection,
        ):
            self.admin.unlock_connector(request, Charger.objects.filter(pk=charger.pk))

        mock_get_connection.assert_not_called()
        messages = [message.message for message in request._messages]
        self.assertTrue(
            any(
                "connector id is required to send UnlockConnector" in message
                for message in messages
            )
        )

    def test_unlock_connector_remote_uses_network_call(self):
        local_node = Node.objects.create(hostname="local-node", mac_address="00:00:00:00:00:03")
        remote_node = Node.objects.create(hostname="remote-node", mac_address="00:00:00:00:00:04")
        charger = Charger.objects.create(
            charger_id="CP-REMOTE",
            connector_id=5,
            node_origin=remote_node,
            allow_remote=True,
        )

        request = self._build_request()
        with (
            patch("ocpp.models.Node.get_local", return_value=local_node),
            patch.object(
                self.admin,
                "_prepare_remote_credentials",
                return_value=(local_node, MagicMock()),
            ) as mock_prepare,
            patch.object(
                self.admin,
                "_call_remote_action",
                return_value=(True, {}),
            ) as mock_call,
        ):
            self.admin.unlock_connector(request, Charger.objects.filter(pk=charger.pk))

        mock_prepare.assert_called_once()
        mock_call.assert_called_once()
        args = mock_call.call_args.args
        self.assertEqual(args[4], "unlock-connector")
        messages = [message.message for message in request._messages]
        self.assertIn("Sent UnlockConnector to 1 charger(s)", messages)

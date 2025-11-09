from __future__ import annotations

import base64
import json
import os
from datetime import timedelta
from unittest.mock import AsyncMock, Mock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from nodes.models import Node, NodeRole
from ocpp import forwarding_service
from ocpp.consumers import CSMSConsumer
from ocpp import store
from ocpp.models import Charger, Transaction
from protocols.models import CPForwarder
from ocpp.tasks import push_forwarded_charge_points
from ocpp.forwarding_service import _REGISTRY_OWNER_PATH, _REGISTRY_OWNER_TTL
from websocket import WebSocketException


class ForwardingTaskTests(TestCase):
    def setUp(self):
        self.role, _ = NodeRole.objects.get_or_create(name="Terminal")
        self.local = Node.objects.create(
            hostname="local",
            address="127.0.0.1",
            port=8000,
            mac_address="00:11:22:33:44:55",
            role=self.role,
            public_endpoint="local",
        )
        self.remote = Node.objects.create(
            hostname="remote",
            address="198.51.100.10",
            port=8443,
            mac_address="00:11:22:33:44:66",
            role=self.role,
            public_endpoint="remote",
        )
        forwarding_service.clear_sessions()
        self.addCleanup(forwarding_service.clear_sessions)
        if _REGISTRY_OWNER_PATH.exists():
            _REGISTRY_OWNER_PATH.unlink()
        self.addCleanup(lambda: _REGISTRY_OWNER_PATH.exists() and _REGISTRY_OWNER_PATH.unlink())

    @patch("protocols.models.send_forwarding_metadata", return_value=(True, None))
    @patch("protocols.models.load_local_node_credentials")
    @patch("ocpp.forwarding_service.create_connection")
    def test_push_forwarded_charge_points_establishes_websocket(
        self, mock_create, mock_credentials, _mock_metadata
    ):
        mock_credentials.return_value = (self.local, object(), None)
        charger = Charger.objects.create(
            charger_id="CP-1001",
            node_origin=self.local,
            manager_node=self.local,
            export_transactions=True,
        )
        forwarder = CPForwarder.objects.create(target_node=self.remote, enabled=True)

        connection = Mock()
        connection.connected = True
        mock_create.return_value = connection

        with patch.object(Node, "get_local", return_value=self.local), patch.object(
            Node,
            "iter_remote_urls",
            lambda self, path: iter(["https://remote.example" + path]),
        ):
            connected = push_forwarded_charge_points()

        self.assertEqual(connected, 1)
        mock_create.assert_called_once()
        session = forwarding_service.get_session(charger.pk)
        self.assertIsNotNone(session)
        updated = Charger.objects.get(pk=charger.pk)
        self.assertEqual(updated.forwarded_to, self.remote)
        self.assertIsNotNone(updated.forwarding_watermark)
        forwarder.refresh_from_db()
        self.assertTrue(forwarder.is_running)
        self.assertEqual(forwarder.last_forwarded_at, updated.forwarding_watermark)

    @patch("protocols.models.send_forwarding_metadata", return_value=(True, None))
    @patch("protocols.models.load_local_node_credentials")
    @patch("ocpp.forwarding_service.create_connection")
    def test_push_forwarded_charge_points_skips_when_owned_elsewhere(
        self, mock_create, mock_credentials, _mock_metadata
    ):
        mock_credentials.return_value = (self.local, object(), None)
        charger = Charger.objects.create(
            charger_id="CP-LOCKED",
            node_origin=self.local,
            manager_node=self.local,
            export_transactions=True,
        )
        CPForwarder.objects.create(target_node=self.remote, enabled=True)

        payload = {
            "owner_id": "other-process",
            "pid": os.getpid(),
            "heartbeat": timezone.now().isoformat(),
        }
        _REGISTRY_OWNER_PATH.parent.mkdir(parents=True, exist_ok=True)
        _REGISTRY_OWNER_PATH.write_text(json.dumps(payload))

        with patch.object(Node, "get_local", return_value=self.local), patch.object(
            Node,
            "iter_remote_urls",
            lambda self, path: iter(["https://remote.example" + path]),
        ):
            connected = push_forwarded_charge_points()

        self.assertEqual(connected, 0)
        mock_create.assert_not_called()
        self.assertIsNone(forwarding_service.get_session(charger.pk))

    @patch("protocols.models.send_forwarding_metadata", return_value=(True, None))
    @patch("protocols.models.load_local_node_credentials")
    @patch("ocpp.forwarding_service.create_connection")
    def test_push_forwarded_charge_points_claims_stale_owner(
        self, mock_create, mock_credentials, _mock_metadata
    ):
        mock_credentials.return_value = (self.local, object(), None)
        connection = Mock()
        connection.connected = True
        mock_create.return_value = connection

        charger = Charger.objects.create(
            charger_id="CP-TAKEOVER",
            node_origin=self.local,
            manager_node=self.local,
            export_transactions=True,
        )
        forwarder = CPForwarder.objects.create(target_node=self.remote, enabled=True)

        stale = timezone.now() - (_REGISTRY_OWNER_TTL + timedelta(minutes=1))
        payload = {
            "owner_id": "stale-process",
            "pid": 999999,
            "heartbeat": stale.isoformat(),
        }
        _REGISTRY_OWNER_PATH.parent.mkdir(parents=True, exist_ok=True)
        _REGISTRY_OWNER_PATH.write_text(json.dumps(payload))

        with patch.object(Node, "get_local", return_value=self.local), patch.object(
            Node,
            "iter_remote_urls",
            lambda self, path: iter(["https://remote.example" + path]),
        ):
            connected = push_forwarded_charge_points()

        self.assertEqual(connected, 1)
        mock_create.assert_called_once()
        self.assertIsNotNone(forwarding_service.get_session(charger.pk))
        forwarder.refresh_from_db()
        self.assertTrue(forwarder.is_running)

    @patch("ocpp.forwarding_service.create_connection")
    def test_push_forwarded_charge_points_skips_active_session(self, mock_create):
        charger = Charger.objects.create(
            charger_id="CP-2002",
            node_origin=self.local,
            manager_node=self.local,
            export_transactions=True,
            forwarded_to=self.remote,
            forwarding_watermark=None,
        )

        connection = Mock()
        connection.connected = True
        mock_create.return_value = connection

        with patch.object(Node, "get_local", return_value=self.local), patch.object(
            Node,
            "iter_remote_urls",
            lambda self, path: iter(["https://remote.example" + path]),
        ):
            first = push_forwarded_charge_points()

        self.assertEqual(first, 1)
        mock_create.assert_called_once()

        mock_create.reset_mock()
        with patch.object(Node, "get_local", return_value=self.local), patch.object(
            Node,
            "iter_remote_urls",
            lambda self, path: iter(["https://remote.example" + path]),
        ):
            second = push_forwarded_charge_points()

        self.assertEqual(second, 0)
        mock_create.assert_not_called()

    @patch("ocpp.forwarding_service.create_connection", side_effect=WebSocketException("boom"))
    def test_push_forwarded_charge_points_reports_failures(self, mock_create):
        charger = Charger.objects.create(
            charger_id="CP-FAIL",
            node_origin=self.local,
            manager_node=self.local,
            export_transactions=True,
            forwarded_to=self.remote,
            forwarding_watermark=None,
        )

        with patch.object(Node, "get_local", return_value=self.local), patch.object(
            Node,
            "iter_remote_urls",
            lambda self, path: iter(["https://remote.example" + path]),
        ):
            result = push_forwarded_charge_points()

        self.assertEqual(result, 0)
        self.assertIsNone(forwarding_service.get_session(charger.pk))

    @patch("protocols.models.send_forwarding_metadata", return_value=(True, None))
    @patch("protocols.models.load_local_node_credentials")
    @patch("ocpp.forwarding_service.create_connection")
    def test_forwarder_sync_opens_session_without_celery(
        self, mock_create, mock_credentials, _mock_metadata
    ):
        mock_credentials.return_value = (self.local, object(), None)
        connection = Mock()
        connection.connected = True
        mock_create.return_value = connection

        charger = Charger.objects.create(
            charger_id="CP-DIRECT",
            node_origin=self.local,
            manager_node=self.local,
            export_transactions=True,
        )
        forwarder = CPForwarder.objects.create(target_node=self.remote, enabled=True)

        with patch.object(Node, "get_local", return_value=self.local), patch.object(
            Node,
            "iter_remote_urls",
            lambda self, path: iter(["https://remote.example" + path]),
        ):
            forwarder.sync_chargers()

        session = forwarding_service.get_session(charger.pk)
        self.assertIsNotNone(session)
        forwarder.refresh_from_db()
        self.assertTrue(forwarder.is_running)
        self.assertIn("Forwarding websocket is connected.", forwarder.last_status)

    @patch("protocols.models.send_forwarding_metadata", return_value=(True, None))
    @patch("protocols.models.load_local_node_credentials")
    @patch("ocpp.forwarding_service.create_connection")
    def test_forwarder_disable_closes_session(
        self, mock_create, mock_credentials, _mock_metadata
    ):
        mock_credentials.return_value = (self.local, object(), None)
        connection = Mock()
        connection.connected = True
        mock_create.return_value = connection

        charger = Charger.objects.create(
            charger_id="CP-DISABLE",
            node_origin=self.local,
            manager_node=self.local,
            export_transactions=True,
        )
        forwarder = CPForwarder.objects.create(target_node=self.remote, enabled=True)

        with patch.object(Node, "get_local", return_value=self.local), patch.object(
            Node,
            "iter_remote_urls",
            lambda self, path: iter(["https://remote.example" + path]),
        ):
            forwarder.sync_chargers()

        self.assertIsNotNone(forwarding_service.get_session(charger.pk))

        forwarder.enabled = False
        forwarder.save()

        self.assertIsNone(forwarding_service.get_session(charger.pk))
        forwarder.refresh_from_db()
        self.assertFalse(forwarder.is_running)
        self.assertIn("Cleared forwarding", forwarder.last_status)

    @patch("protocols.models.send_forwarding_metadata", return_value=(True, None))
    @patch("protocols.models.load_local_node_credentials")
    @patch("ocpp.forwarding_service.create_connection")
    def test_forwarder_delete_closes_session(
        self, mock_create, mock_credentials, _mock_metadata
    ):
        mock_credentials.return_value = (self.local, object(), None)
        connection = Mock()
        connection.connected = True
        mock_create.return_value = connection

        charger = Charger.objects.create(
            charger_id="CP-DELETE",
            node_origin=self.local,
            manager_node=self.local,
            export_transactions=True,
        )
        forwarder = CPForwarder.objects.create(target_node=self.remote, enabled=True)

        with patch.object(Node, "get_local", return_value=self.local), patch.object(
            Node,
            "iter_remote_urls",
            lambda self, path: iter(["https://remote.example" + path]),
        ):
            forwarder.sync_chargers()

        self.assertIsNotNone(forwarding_service.get_session(charger.pk))

        forwarder.delete()

        self.assertIsNone(forwarding_service.get_session(charger.pk))
        charger.refresh_from_db()
        self.assertIsNone(charger.forwarded_to)


class ForwardingConsumerSyncTests(TestCase):
    def setUp(self):
        self.role, _ = NodeRole.objects.get_or_create(name="Terminal")
        self.local = Node.objects.create(
            hostname="local-consumer",
            address="127.0.0.1",
            port=9000,
            mac_address="00:11:22:33:44:77",
            role=self.role,
            public_endpoint="local-consumer",
        )
        self.remote = Node.objects.create(
            hostname="remote-consumer",
            address="198.51.100.20",
            port=8443,
            mac_address="00:11:22:33:44:88",
            role=self.role,
            public_endpoint="remote-consumer",
        )
        store.connections.clear()
        store.ip_connections.clear()
        store.logs["charger"].clear()
        forwarding_service.clear_sessions()

    @patch("ocpp.consumers.sync_forwarded_charge_points")
    def test_reconnect_triggers_forwarding_sync(self, mock_sync):
        mock_sync.return_value = 0
        charger = Charger.objects.create(
            charger_id="CP-RECON",
            node_origin=self.local,
            manager_node=self.local,
            export_transactions=True,
            forwarded_to=self.remote,
        )

        consumer = CSMSConsumer()
        consumer.scope = {
            "query_string": b"",
            "url_route": {"kwargs": {"cid": charger.charger_id}},
            "headers": [],
            "client": ("203.0.113.10", 12345),
            "subprotocols": [],
            "path": f"/ws/ocpp/{charger.charger_id}",
        }
        consumer.channel_name = "test-channel"
        consumer.channel_layer = Mock()
        consumer.accept = AsyncMock(return_value=None)
        consumer.close = AsyncMock(return_value=None)
        consumer.send = AsyncMock(return_value=None)

        with patch.object(Node, "get_local", return_value=self.local), patch(
            "ocpp.consumers.store.register_ip_connection",
            return_value=True,
        ), patch("ocpp.consumers.store.add_log"), patch(
            "ocpp.consumers.store.register_log_name"
        ), patch(
            "ocpp.consumers.ChargerConfiguration.objects.filter"
        ) as mock_config_filter, patch(
            "ocpp.consumers.CPFirmware.objects.filter"
        ) as mock_fw_filter, patch(
            "ocpp.consumers.Charger.objects.get_or_create"
        ) as mock_get_or_create, patch.object(
            Charger,
            "refresh_manager_node",
            return_value=None,
        ):
            mock_config_filter.return_value.exists.return_value = True
            mock_fw_filter.return_value.exists.return_value = True
            mock_get_or_create.return_value = (charger, False)
            async_to_sync(consumer.connect)()

        mock_sync.assert_called_once_with(refresh_forwarders=False)


class ForwardingViewTests(TestCase):
    def setUp(self):
        self.role, _ = NodeRole.objects.get_or_create(name="Hub")
        self.remote = Node.objects.create(
            hostname="remote",
            address="203.0.113.5",
            port=443,
            mac_address="00:aa:bb:cc:dd:01",
            role=self.role,
            public_endpoint="remote",
        )
        self.local = Node.objects.create(
            hostname="local",
            address="10.0.0.5",
            port=8001,
            mac_address="00:aa:bb:cc:dd:02",
            role=self.role,
            public_endpoint="local",
        )
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.remote.public_key = (
            self.private_key.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )
        self.remote.save()

    def _signed_payload(self, payload: dict) -> dict[str, str]:
        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        signature = base64.b64encode(
            self.private_key.sign(
                payload_json.encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        ).decode()
        return {"data": payload_json, "signature": signature}

    def test_forward_chargers_creates_metadata_and_transactions(self):
        payload = {
            "requester": str(self.remote.uuid),
            "chargers": [
                {
                    "charger_id": "FORWARD-1",
                    "connector_id": None,
                    "allow_remote": False,
                    "export_transactions": True,
                    "last_meter_values": {},
                }
            ],
            "transactions": {
                "chargers": [
                    {"charger_id": "FORWARD-1", "connector_id": None, "require_rfid": False}
                ],
                "transactions": [
                    {
                        "charger": "FORWARD-1",
                        "connector_id": None,
                        "account": None,
                        "rfid": "",
                        "vid": "",
                        "vin": "",
                        "meter_start": 10,
                        "meter_stop": 20,
                        "start_time": timezone.now().isoformat(),
                        "stop_time": None,
                        "received_start_time": timezone.now().isoformat(),
                        "received_stop_time": None,
                        "meter_values": [],
                    }
                ],
            },
        }
        signed = self._signed_payload(payload)
        response = self.client.post(
            reverse("node-network-forward-chargers"),
            data=signed["data"],
            content_type="application/json",
            HTTP_X_SIGNATURE=signed["signature"],
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "ok")
        charger = Charger.objects.get(charger_id="FORWARD-1")
        self.assertEqual(charger.node_origin, self.remote)
        self.assertEqual(Transaction.objects.filter(charger=charger).count(), 1)

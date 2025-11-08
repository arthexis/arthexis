from __future__ import annotations

import base64
import json
from unittest.mock import Mock, patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from nodes.models import Node, NodeRole
from ocpp import tasks as ocpp_tasks
from ocpp.models import Charger, Transaction
from protocols.models import CPForwarder
from ocpp.tasks import push_forwarded_charge_points
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
        ocpp_tasks._FORWARDING_SESSIONS.clear()
        self.addCleanup(ocpp_tasks._FORWARDING_SESSIONS.clear)

    @patch("protocols.models.send_forwarding_metadata", return_value=(True, None))
    @patch("protocols.models.load_local_node_credentials")
    @patch("ocpp.tasks.create_connection")
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
        session = ocpp_tasks._FORWARDING_SESSIONS.get(charger.pk)
        self.assertIsNotNone(session)
        updated = Charger.objects.get(pk=charger.pk)
        self.assertEqual(updated.forwarded_to, self.remote)
        self.assertIsNotNone(updated.forwarding_watermark)
        forwarder.refresh_from_db()
        self.assertTrue(forwarder.is_running)
        self.assertEqual(forwarder.last_forwarded_at, updated.forwarding_watermark)

    @patch("ocpp.tasks.create_connection")
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

    @patch("ocpp.tasks.create_connection", side_effect=WebSocketException("boom"))
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
        self.assertIsNone(ocpp_tasks._FORWARDING_SESSIONS.get(charger.pk))


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

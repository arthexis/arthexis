from __future__ import annotations

import base64
import json
from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from nodes.models import Node, NodeRole
from ocpp.models import Charger, Transaction
from ocpp.tasks import push_forwarded_charge_points


class ForwardingTaskTests(TestCase):
    def setUp(self):
        self.role = NodeRole.objects.create(name="Terminal")
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
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_bytes = self.private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        self.local.public_key = public_bytes.decode()
        self.local.save()

    @patch("requests.post")
    def test_push_forwarded_charge_points_sends_transactions(self, mock_post):
        charger = Charger.objects.create(
            charger_id="CP-1001",
            node_origin=self.local,
            manager_node=self.local,
            export_transactions=True,
            forwarded_to=self.remote,
            forwarding_watermark=timezone.now() - timedelta(minutes=1),
        )
        tx = Transaction.objects.create(
            charger=charger,
            start_time=timezone.now(),
            connector_id=1,
            meter_start=10,
            meter_stop=20,
        )

        mock_post.return_value = Mock(
            ok=True,
            status_code=200,
            json=Mock(return_value={"status": "ok"}),
        )

        with patch.object(Node, "get_local", return_value=self.local), patch.object(
            Node, "get_private_key", return_value=self.private_key
        ), patch.object(
            Node,
            "iter_remote_urls",
            lambda self, path: iter(["https://remote.example" + path]),
        ):
            forwarded = push_forwarded_charge_points()

        self.assertEqual(forwarded, 1)
        self.assertTrue(mock_post.called)
        payload = json.loads(mock_post.call_args.kwargs["data"])
        self.assertEqual(payload.get("chargers"), [])
        transactions_payload = payload.get("transactions", {})
        self.assertEqual(len(transactions_payload.get("transactions", [])), 1)
        updated = Charger.objects.get(pk=charger.pk)
        self.assertEqual(updated.forwarding_watermark, tx.start_time)

    @patch("requests.post")
    def test_push_forwarded_charge_points_sends_metadata_when_uninitialized(self, mock_post):
        charger = Charger.objects.create(
            charger_id="CP-INIT",
            node_origin=self.local,
            manager_node=self.local,
            export_transactions=True,
            forwarded_to=self.remote,
            forwarding_watermark=None,
        )

        mock_post.return_value = Mock(
            ok=True,
            status_code=200,
            json=Mock(return_value={"status": "ok"}),
        )

        with patch.object(Node, "get_local", return_value=self.local), patch.object(
            Node, "get_private_key", return_value=self.private_key
        ), patch.object(
            Node,
            "iter_remote_urls",
            lambda self, path: iter(["https://remote.example" + path]),
        ):
            forwarded = push_forwarded_charge_points()

        self.assertEqual(forwarded, 0)
        payload = json.loads(mock_post.call_args.kwargs["data"])
        chargers_payload = payload.get("chargers", [])
        self.assertEqual(len(chargers_payload), 1)
        updated = Charger.objects.get(pk=charger.pk)
        self.assertIsNotNone(updated.forwarding_watermark)


class ForwardingViewTests(TestCase):
    def setUp(self):
        self.role = NodeRole.objects.create(name="Hub")
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

import base64
import json
import tempfile
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from nodes.models import Node

from ocpp.models import Charger, Transaction
from ocpp.remote import apply_remote_snapshot
from ocpp.tasks import sync_remote_chargers


class RemoteSnapshotViewTests(TestCase):
    def setUp(self):
        self.remote_private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        self.remote_public_key = self.remote_private_key.public_key()
        self.remote_node = Node.objects.create(
            hostname="remote",
            address="10.0.0.2",
            port=8000,
            mac_address="00:aa:bb:cc:dd:02",
            public_key=self.remote_public_key.public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode(),
        )
        self.local_node = Node.objects.create(
            hostname="local",
            address="127.0.0.1",
            port=8000,
            mac_address=Node.get_current_mac(),
        )
        self.charger = Charger.objects.create(
            charger_id="CP-001",
            public_display=True,
            last_status="Available",
            last_status_timestamp=timezone.now(),
        )
        self.transaction = Transaction.objects.create(
            charger=self.charger,
            start_time=timezone.now(),
            connector_id=None,
        )
        other_node = Node.objects.create(
            hostname="remote-child",
            address="10.0.0.3",
            port=8000,
            mac_address="00:aa:bb:cc:dd:03",
        )
        Charger.objects.create(
            charger_id="REMOTE-COPY",
            public_display=True,
            node_origin=other_node,
        )

    def _sign(self, body: bytes) -> str:
        return base64.b64encode(
            self.remote_private_key.sign(body, padding.PKCS1v15(), hashes.SHA256())
        ).decode()

    def test_remote_snapshot_requires_signature(self):
        url = reverse("remote-charger-snapshot")
        response = self.client.post(
            url,
            data=json.dumps({"requester": str(self.remote_node.uuid)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_remote_snapshot_returns_public_chargers(self):
        url = reverse("remote-charger-snapshot")
        payload = json.dumps({"requester": str(self.remote_node.uuid)}).encode()
        response = self.client.post(
            url,
            data=payload,
            content_type="application/json",
            HTTP_X_SIGNATURE=self._sign(payload),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("chargers", data)
        self.assertEqual(len(data["chargers"]), 1)
        charger_payload = data["chargers"][0]
        self.assertEqual(charger_payload["charger_id"], "CP-001")
        self.assertEqual(len(charger_payload["transactions"]), 1)


class RemoteSnapshotApplyTests(TestCase):
    def setUp(self):
        self.remote_node = Node.objects.create(
            hostname="remote",
            address="10.0.0.2",
            port=8000,
            mac_address="00:aa:bb:cc:dd:04",
        )

    def test_apply_remote_snapshot_creates_and_updates(self):
        start = timezone.now()
        payload = {
            "chargers": [
                {
                    "charger_id": "CP-900",
                    "connector_id": 1,
                    "display_name": "Remote Charger",
                    "public_display": True,
                    "require_rfid": True,
                    "language": "en",
                    "last_status": "Available",
                    "last_status_timestamp": start.isoformat(),
                    "transactions": [
                        {
                            "connector_id": 1,
                            "start_time": start.isoformat(),
                            "meter_start": 100,
                            "meter_stop": 150,
                            "rfid": "ABC123",
                        }
                    ],
                }
            ]
        }

        created, updated, synced = apply_remote_snapshot(self.remote_node, payload)
        self.assertEqual(created, 1)
        self.assertEqual(updated, 0)
        self.assertEqual(synced, 1)

        charger = Charger.objects.get(charger_id="CP-900", connector_id=1)
        self.assertEqual(charger.node_origin, self.remote_node)
        self.assertTrue(charger.require_rfid)

        transaction = Transaction.objects.get(charger=charger)
        self.assertEqual(transaction.meter_start, 100)
        self.assertEqual(transaction.meter_stop, 150)

        payload["chargers"][0]["display_name"] = "Updated"
        created, updated, synced = apply_remote_snapshot(self.remote_node, payload)
        self.assertEqual(created, 0)
        self.assertEqual(updated, 1)
        self.assertEqual(synced, 1)
        charger.refresh_from_db()
        self.assertEqual(charger.display_name, "Updated")


class RemoteSyncTaskTests(TestCase):
    def setUp(self):
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.public_key = self.private_key.public_key()
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)

        base_path = Path(self.tempdir.name)
        security_dir = base_path / "security"
        security_dir.mkdir(parents=True, exist_ok=True)
        (security_dir / "local-endpoint").write_bytes(
            self.private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )

        self.local_node = Node.objects.create(
            hostname="local",
            address="127.0.0.1",
            port=8000,
            mac_address=Node.get_current_mac(),
            public_endpoint="local-endpoint",
            base_path=self.tempdir.name,
            public_key=self.public_key.public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode(),
        )

        self.remote_node = Node.objects.create(
            hostname="remote",
            address="10.0.0.5",
            port=8000,
            mac_address="00:aa:bb:cc:dd:05",
            public_key="remote",
        )

    @mock.patch("ocpp.remote.requests.post")
    def test_sync_remote_chargers(self, mock_post):
        now = timezone.now()
        response = mock.Mock()
        response.status_code = 200
        response.json.return_value = {
            "chargers": [
                {
                    "charger_id": "REMOTE-1",
                    "display_name": "Remote One",
                    "public_display": True,
                    "transactions": [
                        {
                            "start_time": now.isoformat(),
                            "meter_start": 10,
                        }
                    ],
                }
            ]
        }
        mock_post.return_value = response

        total = sync_remote_chargers()

        self.assertEqual(total, 1)
        charger = Charger.objects.get(charger_id="REMOTE-1")
        self.assertEqual(charger.node_origin, self.remote_node)
        self.assertTrue(Transaction.objects.filter(charger=charger).exists())

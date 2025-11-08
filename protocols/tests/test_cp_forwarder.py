from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from nodes.models import Node, NodeRole
from ocpp.models import Charger
from protocols.models import CPForwarder


class CPForwarderTests(TestCase):
    def setUp(self):
        self.role, _ = NodeRole.objects.get_or_create(name="Terminal")
        self.local = Node.objects.create(
            hostname="local",
            address="127.0.0.1",
            port=8000,
            mac_address=Node.get_current_mac(),
            role=self.role,
        )
        self.remote = Node.objects.create(
            hostname="remote",
            address="192.0.2.5",
            port=8443,
            mac_address="00:11:22:33:44:aa",
            role=self.role,
        )

    @patch("protocols.models.send_forwarding_metadata", return_value=(True, None))
    @patch("protocols.models.load_local_node_credentials")
    def test_enabling_forwarder_updates_chargers(
        self, mock_credentials, mock_metadata
    ):
        mock_credentials.return_value = (self.local, object(), None)
        charger = Charger.objects.create(
            charger_id="CP-FWD-1",
            export_transactions=True,
            node_origin=self.local,
        )

        forwarder = CPForwarder.objects.create(target_node=self.remote, enabled=True)

        charger.refresh_from_db()
        forwarder.refresh_from_db()
        self.assertEqual(charger.forwarded_to, self.remote)
        self.assertIn("Forwarding", forwarder.last_status)
        self.assertEqual(forwarder.last_error, "")
        mock_metadata.assert_called_once()

    @patch("protocols.models.send_forwarding_metadata", return_value=(True, None))
    @patch("protocols.models.load_local_node_credentials")
    def test_disabling_forwarder_clears_forwarding(
        self, mock_credentials, mock_metadata
    ):
        mock_credentials.return_value = (self.local, object(), None)
        charger = Charger.objects.create(
            charger_id="CP-FWD-2",
            export_transactions=True,
            node_origin=self.local,
        )

        forwarder = CPForwarder.objects.create(target_node=self.remote, enabled=True)
        forwarder.enabled = False
        forwarder.save()

        charger.refresh_from_db()
        forwarder.refresh_from_db()
        self.assertIsNone(charger.forwarded_to)
        self.assertFalse(forwarder.is_running)

    @patch("protocols.models.load_local_node_credentials")
    def test_sync_chargers_records_credential_error(self, mock_credentials):
        mock_credentials.return_value = (self.local, None, "missing key")
        Charger.objects.create(
            charger_id="CP-FWD-3",
            export_transactions=True,
            node_origin=self.local,
        )

        forwarder = CPForwarder.objects.create(target_node=self.remote, enabled=True)
        forwarder.refresh_from_db()
        self.assertIn("Forwarding", forwarder.last_status)
        self.assertEqual(forwarder.last_error, "missing key")

    def test_running_state_helpers(self):
        forwarder = CPForwarder.objects.create(target_node=self.remote, enabled=False)
        timestamp = timezone.now()

        forwarder.mark_running(timestamp)
        forwarder.refresh_from_db()
        self.assertTrue(forwarder.is_running)
        self.assertEqual(forwarder.last_forwarded_at, timestamp)

        forwarder.set_running_state(False)
        forwarder.refresh_from_db()
        self.assertFalse(forwarder.is_running)

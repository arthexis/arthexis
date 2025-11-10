from __future__ import annotations

import json

import tests.conftest  # noqa: F401
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from nodes.models import Node, NodeRole
from ocpp.models import Charger
from protocols.admin import CPForwarderForm
from protocols.forwarding import send_forwarding_metadata
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

    @patch("protocols.models.is_target_active", return_value=True)
    @patch("protocols.models.sync_forwarded_charge_points")
    @patch("protocols.models.send_forwarding_metadata", return_value=(True, None))
    @patch("protocols.models.load_local_node_credentials")
    def test_enabling_forwarder_updates_chargers(
        self, mock_credentials, _mock_metadata, mock_sync, _mock_target
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
        args, kwargs = _mock_metadata.call_args
        self.assertIn("forwarded_messages", kwargs)
        self.assertEqual(
            kwargs["forwarded_messages"], forwarder.get_forwarded_messages()
        )
        mock_sync.assert_called()

    @patch("protocols.models.sync_forwarded_charge_points")
    @patch("protocols.models.send_forwarding_metadata", return_value=(True, None))
    @patch("protocols.models.load_local_node_credentials")
    def test_disabling_forwarder_clears_forwarding(
        self, mock_credentials, _mock_metadata, mock_sync
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
        mock_sync.assert_called()

    @patch("protocols.models.is_target_active", return_value=False)
    @patch("protocols.models.sync_forwarded_charge_points")
    @patch("protocols.models.load_local_node_credentials")
    def test_sync_chargers_records_credential_error(
        self, mock_credentials, mock_sync, _mock_target
    ):
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
        mock_sync.assert_called()

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

    def test_forwarded_messages_default(self):
        forwarder = CPForwarder.objects.create(target_node=self.remote, enabled=True)
        self.assertEqual(
            forwarder.get_forwarded_messages(),
            list(CPForwarder.available_forwarded_messages()),
        )

    def test_forwarded_messages_respects_selection(self):
        forwarder = CPForwarder.objects.create(
            target_node=self.remote,
            forwarded_messages=["Authorize", "BootNotification"],
        )
        self.assertEqual(
            forwarder.forwarded_messages,
            ["Authorize", "BootNotification"],
        )
        self.assertTrue(forwarder.forwards_action("Authorize"))
        self.assertFalse(forwarder.forwards_action("Heartbeat"))

    @patch("protocols.forwarding.requests.post")
    def test_send_forwarding_metadata_includes_forwarded_messages(self, mock_post):
        response = mock_post.return_value
        response.ok = True
        response.status_code = 200
        response.json.return_value = {"status": "ok"}

        charger = Charger.objects.create(charger_id="META-1")
        forwarded = ["Authorize", "BootNotification"]

        success, error = send_forwarding_metadata(
            self.remote,
            [charger],
            self.local,
            None,
            forwarded_messages=forwarded,
        )

        self.assertTrue(success)
        self.assertIsNone(error)
        payload = json.loads(mock_post.call_args.kwargs.get("data", "{}"))
        charger_payload = payload.get("chargers", [{}])[0]
        self.assertEqual(charger_payload.get("forwarded_messages"), forwarded)


class CPForwarderAdminFormTests(TestCase):
    def test_form_defaults_to_all_messages(self):
        form = CPForwarderForm()
        self.assertCountEqual(
            form.fields["forwarded_messages"].initial,
            CPForwarder.available_forwarded_messages(),
        )

    def test_form_clean_sanitizes_selection(self):
        form = CPForwarderForm()
        form.cleaned_data = {"forwarded_messages": ["Authorize", "Unknown"]}
        cleaned = form.clean_forwarded_messages()
        self.assertEqual(cleaned, ["Authorize"])

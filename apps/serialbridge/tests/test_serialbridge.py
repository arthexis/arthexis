from datetime import timedelta
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.serialbridge.models import (
    SerialCommandAudit,
    SerialInterface,
    SerialPeer,
    SerialSession,
)


class SerialBridgePingCommandTests(TestCase):
    def test_ping_records_session_and_audit(self):
        interface = SerialInterface.objects.create(
            name="ttyS0", device_path="/dev/ttyUSB1", is_enabled=True
        )
        peer = SerialPeer.objects.create(
            interface=interface,
            node_id="node-b",
            shared_key_fingerprint="abc123",
        )

        output = StringIO()
        call_command(
            "serialbridge", "ping", interface="ttyS0", peer="node-b", stdout=output
        )

        session = SerialSession.objects.get(interface=interface, peer=peer)
        self.assertEqual(session.status, SerialSession.Status.CONNECTED)
        self.assertEqual(session.tx_messages, 1)
        self.assertEqual(session.rx_messages, 1)
        peer.refresh_from_db()
        self.assertEqual(peer.last_seen_at, session.last_seen_at)

        audit = SerialCommandAudit.objects.get(interface=interface, peer=peer)
        self.assertEqual(audit.command, SerialCommandAudit.CommandType.PING)
        self.assertEqual(audit.result, SerialCommandAudit.Result.SUCCESS)
        self.assertIn("Ping recorded", output.getvalue())

    def test_ping_updates_existing_session_timestamp(self):
        interface = SerialInterface.objects.create(
            name="ttyS0", device_path="/dev/ttyUSB1", is_enabled=True
        )
        peer = SerialPeer.objects.create(
            interface=interface,
            node_id="node-b",
            shared_key_fingerprint="abc123",
        )
        session = SerialSession.objects.create(interface=interface, peer=peer)
        stale_timestamp = timezone.now() - timedelta(days=1)
        SerialSession.objects.filter(pk=session.pk).update(updated_at=stale_timestamp)

        call_command("serialbridge", "ping", interface="ttyS0", peer="node-b")

        session.refresh_from_db()
        peer.refresh_from_db()
        self.assertGreater(session.updated_at, stale_timestamp)
        self.assertEqual(peer.last_seen_at, session.last_seen_at)
        self.assertEqual(session.tx_messages, 1)
        self.assertEqual(session.rx_messages, 1)

    def test_duplicate_session_for_interface_peer_is_rejected(self):
        interface = SerialInterface.objects.create(
            name="ttyS0", device_path="/dev/ttyUSB1", is_enabled=True
        )
        peer = SerialPeer.objects.create(
            interface=interface,
            node_id="node-b",
            shared_key_fingerprint="abc123",
        )
        SerialSession.objects.create(interface=interface, peer=peer)

        with self.assertRaises(ValidationError):
            SerialSession.objects.create(interface=interface, peer=peer)

    def test_ping_fails_when_interface_not_enabled(self):
        SerialInterface.objects.create(
            name="ttyS0", device_path="/dev/ttyUSB1", is_enabled=False
        )
        with self.assertRaises(CommandError):
            call_command("serialbridge", "ping", interface="ttyS0", peer="node-b")

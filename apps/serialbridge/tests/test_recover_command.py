from io import StringIO
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.serialbridge.models import SerialCommandAudit, SerialInterface, SerialPeer


class SerialBridgeRecoverCommandTests(TestCase):
    def setUp(self):
        self.interface = SerialInterface.objects.create(
            name="ttyS0", device_path="/dev/ttyUSB0", is_enabled=True
        )
        self.peer = SerialPeer.objects.create(
            interface=self.interface, node_id="node-b", shared_key_fingerprint="abc123"
        )

    def test_diagnostics_manifest_creates_audit(self):
        call_command(
            "serialbridge",
            "recover",
            interface="ttyS0",
            peer="node-b",
            operation="diagnostics_manifest",
        )
        audit = SerialCommandAudit.objects.latest("id")
        self.assertEqual(audit.command, SerialCommandAudit.CommandType.DIAGNOSTICS)

    def test_tail_logs_is_bounded(self):
        temp_log = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
        path = Path(temp_log.name)
        temp_log.close()
        self.addCleanup(lambda: path.unlink(missing_ok=True))

        with path.open("w", encoding="utf-8") as handle:
            for i in range(400):
                handle.write(f"line-{i}\n")
        output = StringIO()
        with self.settings(LOG_DIR=path.parent, LOG_FILE_NAME=path.name):
            call_command(
                "serialbridge",
                "recover",
                interface="ttyS0",
                peer="node-b",
                operation="tail_logs",
                log_path=str(path),
                line_count=500,
                stdout=output,
            )
        self.assertIn("Returned 200 log lines", output.getvalue())

    @patch("apps.serialbridge.management.commands.serialbridge.subprocess.run")
    def test_restart_service_allowlist(self, mock_run):
        call_command(
            "serialbridge",
            "recover",
            interface="ttyS0",
            peer="node-b",
            operation="restart_service",
            service="arthexis",
        )
        mock_run.assert_called_once()

    def test_restart_service_disallowed(self):
        with self.assertRaises(CommandError):
            call_command(
                "serialbridge",
                "recover",
                interface="ttyS0",
                peer="node-b",
                operation="restart_service",
                service="ssh",
            )

    @patch("apps.serialbridge.management.commands.serialbridge.subprocess.run")
    def test_restore_network_allowed_interfaces(self, mock_run):
        call_command(
            "serialbridge",
            "recover",
            interface="ttyS0",
            peer="node-b",
            restore_network="eth0",
        )
        call_command(
            "serialbridge",
            "recover",
            interface="ttyS0",
            peer="node-b",
            restore_network="wlan1",
        )
        called_commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertIn(
            ["nmcli", "device", "set", "eth0", "managed", "yes"], called_commands
        )
        self.assertIn(
            ["nmcli", "device", "set", "wlan1", "managed", "yes"], called_commands
        )

    @patch(
        "apps.serialbridge.management.commands.serialbridge.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["sudo"], timeout=20),
    )
    def test_restart_service_wraps_timeout(self, _mock_run):
        with self.assertRaises(CommandError):
            call_command(
                "serialbridge",
                "recover",
                interface="ttyS0",
                peer="node-b",
                operation="restart_service",
                service="arthexis",
            )

    @patch(
        "apps.serialbridge.management.commands.serialbridge.subprocess.run",
        side_effect=subprocess.CalledProcessError(returncode=1, cmd=["nmcli"]),
    )
    def test_restore_network_wraps_called_process_error(self, _mock_run):
        with self.assertRaises(CommandError):
            call_command(
                "serialbridge",
                "recover",
                interface="ttyS0",
                peer="node-b",
                restore_network="eth0",
            )

    @patch("apps.serialbridge.management.commands.serialbridge.subprocess.run")
    def test_shim_restore_network_without_action(self, _mock_run):
        call_command(
            "serialbridge_recover",
            interface="ttyS0",
            peer="node-b",
            restore_network="eth0",
        )
        self.assertTrue(_mock_run.called)
        audit = SerialCommandAudit.objects.latest("id")
        self.assertEqual(audit.payload.get("action"), "restore_network")

    def test_shim_tail_logs_preserves_defaults(self):
        temp_log = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
        path = Path(temp_log.name)
        temp_log.close()
        self.addCleanup(lambda: path.unlink(missing_ok=True))

        with path.open("w", encoding="utf-8") as handle:
            handle.write("line-1\n")

        with self.settings(LOG_DIR=path.parent, LOG_FILE_NAME=path.name):
            call_command(
                "serialbridge_recover",
                interface="ttyS0",
                peer="node-b",
                action="tail_logs",
            )
        audit = SerialCommandAudit.objects.latest("id")
        self.assertEqual(audit.command, SerialCommandAudit.CommandType.LOG_TAIL)
        self.assertEqual(audit.payload["line_count"], 80)

    def test_restore_network_rejects_unknown_interface(self):
        with self.assertRaises(CommandError):
            call_command(
                "serialbridge",
                "recover",
                interface="ttyS0",
                peer="node-b",
                restore_network="lo",
            )

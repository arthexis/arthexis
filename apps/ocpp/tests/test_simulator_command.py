"""Tests for the ``simulator`` management command."""

from __future__ import annotations

import io
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


class SimulatorCommandTests(SimpleTestCase):
    """Validate command-level simulator lifecycle behavior."""

    @patch("apps.ocpp.management.commands.simulator._start_simulator")
    @patch("apps.ocpp.management.commands.simulator.get_simulator_backend_choices")
    def test_start_forwards_all_ui_options(self, choices_mock, start_mock) -> None:
        """Start action should forward every UI-exposed runtime option."""

        choices_mock.return_value = (("arthexis", "arthexis"), ("mobilityhouse", "mobilityhouse"))
        start_mock.return_value = (True, "Connection accepted", "sim.log")

        out = io.StringIO()
        call_command(
            "simulator",
            "start",
            "--slot",
            "2",
            "--host",
            "localhost",
            "--ws-port",
            "9001",
            "--cp-path",
            "CP-TEST",
            "--serial-number",
            "SER-1",
            "--connector-id",
            "3",
            "--rfid",
            "ABCD",
            "--vin",
            "VIN123",
            "--duration",
            "120",
            "--interval",
            "2.5",
            "--pre-charge-delay",
            "4",
            "--average-kwh",
            "33.3",
            "--amperage",
            "64",
            "--repeat",
            "--username",
            "alice",
            "--password",
            "secret",
            "--backend",
            "mobilityhouse",
            "--simulator-name",
            "Demo Sim",
            "--start-delay",
            "6",
            "--reconnect-slots",
            "1,2",
            "--demo-mode",
            "--meter-interval",
            "1.5",
            "--allow-private-network",
            "--ws-scheme",
            "wss",
            "--use-tls",
            stdout=out,
        )

        start_mock.assert_called_once()
        params = start_mock.call_args.args[0]
        self.assertEqual(start_mock.call_args.kwargs["cp"], 2)
        self.assertEqual(params["host"], "localhost")
        self.assertEqual(params["ws_port"], 9001)
        self.assertEqual(params["cp_path"], "CP-TEST")
        self.assertEqual(params["serial_number"], "SER-1")
        self.assertEqual(params["connector_id"], 3)
        self.assertEqual(params["rfid"], "ABCD")
        self.assertEqual(params["vin"], "VIN123")
        self.assertEqual(params["duration"], 120)
        self.assertEqual(params["interval"], 2.5)
        self.assertEqual(params["pre_charge_delay"], 4.0)
        self.assertEqual(params["average_kwh"], 33.3)
        self.assertEqual(params["amperage"], 64.0)
        self.assertTrue(params["repeat"])
        self.assertEqual(params["username"], "alice")
        self.assertEqual(params["password"], "secret")
        self.assertEqual(params["simulator_backend"], "mobilityhouse")
        self.assertEqual(params["name"], "Demo Sim")
        self.assertEqual(params["start_delay"], 6.0)
        self.assertEqual(params["delay"], 6.0)
        self.assertEqual(params["reconnect_slots"], "1,2")
        self.assertTrue(params["demo_mode"])
        self.assertEqual(params["meter_interval"], 1.5)
        self.assertTrue(params["allow_private_network"])
        self.assertEqual(params["ws_scheme"], "wss")
        self.assertTrue(params["use_tls"])

    @patch("apps.ocpp.management.commands.simulator._start_simulator")
    @patch("apps.ocpp.management.commands.simulator.get_simulator_backend_choices")
    def test_start_defaults_allow_private_network(self, choices_mock, start_mock) -> None:
        """Start action should permit localhost defaults without extra flags."""

        choices_mock.return_value = (("arthexis", "arthexis"),)
        start_mock.return_value = (True, "Connection accepted", "sim.log")

        call_command("simulator", "start")

        params = start_mock.call_args.args[0]
        self.assertTrue(params["allow_private_network"])

    @patch("apps.ocpp.management.commands.simulator._start_simulator")
    @patch("apps.ocpp.management.commands.simulator.get_simulator_backend_choices")
    def test_start_raises_error_when_runtime_rejects_request(self, choices_mock, start_mock) -> None:
        """Start action should return non-zero via CommandError when not started."""

        choices_mock.return_value = (("arthexis", "arthexis"),)
        start_mock.return_value = (False, "Already running", "sim.log")

        with self.assertRaisesMessage(CommandError, "Already running"):
            call_command("simulator", "start")

    @patch("apps.ocpp.management.commands.simulator.get_simulator_backend_choices")
    def test_start_rejects_backend_when_not_enabled(self, choices_mock) -> None:
        """Backend validation should fail with a clear error when disabled."""

        choices_mock.return_value = (("arthexis", "arthexis"),)

        with self.assertRaises(CommandError):
            call_command("simulator", "start", "--backend", "mobilityhouse")

    @patch("apps.ocpp.management.commands.simulator.get_simulator_state")
    def test_status_prints_slot_state_json(self, status_mock) -> None:
        """Status action should print JSON for the selected slot."""

        status_mock.return_value = {"running": False, "last_status": "idle"}
        out = io.StringIO()

        call_command("simulator", "status", "--slot", "2", stdout=out)

        status_mock.assert_called_once_with(cp=2, refresh_file=True)
        rendered = out.getvalue()
        self.assertIn('"running": false', rendered)
        self.assertIn('"last_status": "idle"', rendered)

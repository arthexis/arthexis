from __future__ import annotations

from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from ocpp.models import Charger


class ChargerStatusCommandTests(TestCase):
    def test_lists_all_chargers(self):
        Charger.objects.create(charger_id="CP100", display_name="Main Lobby")
        Charger.objects.create(
            charger_id="CP200",
            display_name="Garage",
            connector_id=2,
            require_rfid=True,
        )

        output = StringIO()
        call_command("charger_status", stdout=output)

        text = output.getvalue()
        self.assertIn("Serial", text)
        self.assertIn("CP100", text)
        self.assertIn("Main Lobby", text)
        self.assertIn("CP200", text)
        self.assertIn("connector", text.lower())

    def test_matches_serial_suffix(self):
        Charger.objects.create(charger_id="EVBOX-12345678")
        Charger.objects.create(charger_id="EVBOX-56789012")

        output = StringIO()
        call_command("charger_status", "--sn", "5678", stdout=output)

        text = output.getvalue()
        self.assertIn("EVBOX-12345678", text)
        self.assertNotIn("EVBOX-56789012", text)

    def test_filters_by_cp_path(self):
        Charger.objects.create(charger_id="CPPATH-01", last_path="/OCPP/ABC123/")
        Charger.objects.create(charger_id="CPPATH-02", last_path="/OCPP/XYZ987/")

        output = StringIO()
        call_command("charger_status", "--cp", "abc123", stdout=output)

        text = output.getvalue()
        self.assertIn("CPPATH-01", text)
        self.assertNotIn("CPPATH-02", text)

    def test_toggles_rfid_requirement(self):
        charger = Charger.objects.create(charger_id="RFID-01", require_rfid=False)

        call_command("charger_status", "--sn", "01", "--rfid-enable")
        charger.refresh_from_db()
        self.assertTrue(charger.require_rfid)

        call_command("charger_status", "--sn", "01", "--rfid-disable")
        charger.refresh_from_db()
        self.assertFalse(charger.require_rfid)

    def test_conflicting_rfid_flags_raise_error(self):
        Charger.objects.create(charger_id="RFID-02")

        with self.assertRaisesMessage(
            CommandError, "Use either --rfid-enable or --rfid-disable"
        ):
            call_command(
                "charger_status",
                "--sn",
                "RFID-02",
                "--rfid-enable",
                "--rfid-disable",
            )

    def test_rfid_toggle_requires_filter(self):
        Charger.objects.create(charger_id="RFID-03")

        with self.assertRaisesMessage(
            CommandError,
            "RFID toggles require selecting at least one charger",
        ):
            call_command("charger_status", "--rfid-enable")

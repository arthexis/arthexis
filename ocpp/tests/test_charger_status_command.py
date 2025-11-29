from __future__ import annotations

from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from ocpp import store
from ocpp.models import Charger, Transaction


class ChargerStatusCommandTests(TestCase):
    def test_lists_all_chargers(self):
        Charger.objects.create(
            charger_id="CP100",
            display_name="Main Lobby",
            last_status="Available",
        )
        connector = Charger.objects.create(
            charger_id="CP200",
            display_name="Garage",
            connector_id=2,
            require_rfid=True,
            last_status="Charging",
        )
        Transaction.objects.create(
            charger=connector,
            connector_id=2,
            start_time=timezone.now(),
            stop_time=timezone.now(),
            meter_start=1000,
            meter_stop=2500,
        )

        output = StringIO()
        call_command("charger_status", stdout=output)

        text = output.getvalue()
        self.assertIn("Serial", text)
        self.assertIn("CP100", text)
        self.assertIn("Main Lobby", text)
        self.assertIn("CP200", text)
        self.assertIn("connector", text.lower())
        self.assertIn("Status", text)
        self.assertIn("Available", text)
        self.assertIn("Total Energy (kWh)", text)
        self.assertIn("Last Contact", text)
        self.assertIn("1.50", text)
        self.assertNotIn("Connected", text)

    def test_last_contact_prefers_latest_heartbeat(self):
        last_heartbeat = timezone.now().replace(microsecond=0)
        Charger.objects.create(
            charger_id="CP-HB",
            last_heartbeat=last_heartbeat,
        )

        output = StringIO()
        call_command("charger_status", stdout=output)

        expected_timestamp = timezone.localtime(last_heartbeat).isoformat()
        self.assertIn(expected_timestamp, output.getvalue())

    def test_last_contact_uses_meter_value_timestamp(self):
        meter_timestamp = timezone.now().replace(microsecond=0)
        Charger.objects.create(
            charger_id="CP-MV",
            last_meter_values={
                "meterValue": [
                    {
                        "timestamp": meter_timestamp.isoformat(),
                        "sampledValue": [{"value": "5"}],
                    }
                ]
            },
        )

        output = StringIO()
        call_command("charger_status", stdout=output)

        expected_timestamp = timezone.localtime(meter_timestamp).isoformat()
        self.assertIn(expected_timestamp, output.getvalue())

    def test_active_connector_displays_rfid_value(self):
        charger = Charger.objects.create(
            charger_id="CP-RFID",
            last_status="Charging",
        )
        connector = Charger.objects.create(
            charger_id="CP-RFID",
            connector_id=1,
            last_status="Charging",
        )
        tx = Transaction.objects.create(
            charger=connector,
            connector_id=1,
            start_time=timezone.now(),
            rfid="abc123",
        )
        self.addCleanup(store.transactions.clear)
        store.set_transaction(connector.charger_id, connector.connector_id, tx)

        output = StringIO()
        call_command("charger_status", stdout=output)

        lines = [
            line.split()
            for line in output.getvalue().splitlines()
            if line.startswith("CP-RFID")
        ]
        self.assertGreaterEqual(len(lines), 2)
        aggregate_fields = next(fields for fields in lines if fields[2] == "all")
        connector_fields = next(fields for fields in lines if fields[2] == "A")
        self.assertEqual(aggregate_fields[3], "off")
        self.assertEqual(connector_fields[3], "ABC123")

    def test_aggregate_connector_matches_sum_of_connectors(self):
        Charger.objects.create(charger_id="CP-AGG")
        connector_one = Charger.objects.create(
            charger_id="CP-AGG",
            connector_id=1,
        )
        connector_two = Charger.objects.create(
            charger_id="CP-AGG",
            connector_id=2,
        )

        Transaction.objects.create(
            charger=connector_one,
            connector_id=1,
            start_time=timezone.now(),
            stop_time=timezone.now(),
            meter_start=1000,
            meter_stop=2000,
        )
        Transaction.objects.create(
            charger=connector_two,
            connector_id=2,
            start_time=timezone.now(),
            stop_time=timezone.now(),
            meter_start=2000,
            meter_stop=2500,
        )

        output = StringIO()
        call_command("charger_status", stdout=output)

        totals: dict[str, float] = {}
        for line in output.getvalue().splitlines():
            if not line.startswith("CP-AGG"):
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            connector = parts[2]
            for part in reversed(parts):
                try:
                    totals[connector] = float(part)
                except ValueError:
                    continue
                break

        self.assertIn("all", totals)
        self.assertIn("A", totals)
        self.assertIn("B", totals)
        self.assertAlmostEqual(totals["all"], totals["A"] + totals["B"], places=2)

    def test_aggregate_status_reflects_available_connectors(self):
        Charger.objects.create(
            charger_id="CP-AGG-STATUS",
            display_name="Aggregate",
            last_status="Charging",
        )
        charging = Charger.objects.create(
            charger_id="CP-AGG-STATUS",
            connector_id=1,
            display_name="Connector1",
            last_status="Charging",
        )
        Transaction.objects.create(
            charger=charging,
            connector_id=1,
            start_time=timezone.now(),
            meter_start=1000,
        )
        Charger.objects.create(
            charger_id="CP-AGG-STATUS",
            connector_id=2,
            display_name="Connector2",
            last_status="Available",
        )

        output = StringIO()
        call_command("charger_status", stdout=output)

        aggregate_parts = None
        for line in output.getvalue().splitlines():
            parts = line.split()
            if len(parts) < 6:
                continue
            if parts[0] == "CP-AGG-STATUS" and parts[2] == "all":
                aggregate_parts = parts
                break

        self.assertIsNotNone(aggregate_parts)
        self.assertEqual("Available", aggregate_parts[5])

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

    def test_filters_by_connector_id(self):
        Charger.objects.create(charger_id="CP-CONN-1", connector_id=1)
        Charger.objects.create(charger_id="CP-CONN-2", connector_id=2)

        output = StringIO()
        call_command("charger_status", "--cp", "B", stdout=output)

        text = output.getvalue()
        self.assertIn("CP-CONN-2", text)
        self.assertNotIn("CP-CONN-1", text)

    def test_filters_by_connector_all(self):
        Charger.objects.create(charger_id="CP-ALL-AGG", connector_id=None)
        Charger.objects.create(charger_id="CP-ALL-IND", connector_id=1)

        output = StringIO()
        call_command("charger_status", "--cp", "all", stdout=output)

        text = output.getvalue()
        self.assertIn("CP-ALL-AGG", text)
        self.assertNotIn("CP-ALL-IND", text)

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

    def test_tail_option_outputs_recent_logs(self):
        charger = Charger.objects.create(charger_id="TAIL-01", connector_id=2)
        log_id = store.identity_key(charger.charger_id, charger.connector_id)
        self.addCleanup(store.clear_log, log_id)
        store.add_log(log_id, "first", log_type="charger")
        store.add_log(log_id, "second", log_type="charger")

        output = StringIO()
        call_command(
            "charger_status",
            "--sn",
            "TAIL-01",
            "--cp",
            "B",
            "--tail",
            "1",
            stdout=output,
        )

        text = output.getvalue()
        self.assertIn("second", text)
        self.assertNotIn("first", text)

    def test_tail_requires_unique_selection(self):
        Charger.objects.create(charger_id="TAIL-A", connector_id=1)
        Charger.objects.create(charger_id="TAIL-B", connector_id=1)

        with self.assertRaisesMessage(
            CommandError,
            "--tail requires selecting exactly one charger",
        ):
            call_command("charger_status", "--cp", "A", "--tail", "5")

    def test_tail_requires_positive_value(self):
        Charger.objects.create(charger_id="TAIL-C", connector_id=1)

        with self.assertRaisesMessage(
            CommandError,
            "--tail requires a positive number of log entries.",
        ):
            call_command(
                "charger_status", "--sn", "TAIL-C", "--cp", "A", "--tail", "0"
            )

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from ocpp.models import Charger, Transaction
from ocpp.status_display import STATUS_BADGE_MAP


class PyxelViewportCommandTests(TestCase):
    def test_builds_connector_snapshot(self):
        connector = Charger.objects.create(
            charger_id="LOV-01",
            connector_id=1,
            display_name="Garage",
            last_status="Charging",
        )
        Transaction.objects.create(
            charger=connector,
            connector_id=1,
            start_time=timezone.now(),
        )

        output_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, output_dir, ignore_errors=True)

        call_command("pyxel_viewport", "--output-dir", str(output_dir), "--skip-launch")

        data_path = output_dir / "data" / "connectors.json"
        self.assertTrue(data_path.exists())

        payload = json.loads(data_path.read_text())
        self.assertEqual(len(payload["connectors"]), 1)
        entry = payload["connectors"][0]
        self.assertEqual(entry["serial"], "LOV-01")
        self.assertEqual(entry["connector_id"], 1)
        self.assertEqual(entry["status_color"], STATUS_BADGE_MAP["charging"][1])
        self.assertTrue(entry["is_charging"])

    def test_snapshot_includes_instance_flag(self):
        output_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, output_dir, ignore_errors=True)

        call_command("pyxel_viewport", "--output-dir", str(output_dir), "--skip-launch")

        payload = json.loads((output_dir / "data" / "connectors.json").read_text())
        self.assertIn("instance_running", payload)
        self.assertIs(payload["instance_running"], False)

    def test_clears_existing_initialized_output_directory(self):
        connector = Charger.objects.create(
            charger_id="LOV-02",
            connector_id=2,
            last_status="Available",
        )
        Transaction.objects.create(
            charger=connector,
            connector_id=2,
            start_time=timezone.now(),
            stop_time=timezone.now(),
        )

        output_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, output_dir, ignore_errors=True)
        call_command("pyxel_viewport", "--output-dir", str(output_dir), "--skip-launch")
        nested_dir = output_dir / "nested"
        nested_dir.mkdir()
        (nested_dir / "occupied.txt").write_text("busy")

        call_command("pyxel_viewport", "--output-dir", str(output_dir), "--skip-launch")

        self.assertFalse((nested_dir / "occupied.txt").exists())
        self.assertTrue((output_dir / "data" / "connectors.json").exists())

    def test_errors_when_output_directory_contains_unrelated_content(self):
        output_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, output_dir, ignore_errors=True)

        (output_dir / "unrelated.txt").write_text("leave me alone")

        with self.assertRaises(CommandError):
            call_command("pyxel_viewport", "--output-dir", str(output_dir), "--skip-launch")

    def test_errors_when_pyxel_runner_missing(self):
        connector = Charger.objects.create(
            charger_id="LOV-02",
            connector_id=2,
            last_status="Available",
        )
        Transaction.objects.create(
            charger=connector,
            connector_id=2,
            start_time=timezone.now(),
            stop_time=timezone.now(),
        )

        with self.assertRaises(CommandError):
            call_command(
                "pyxel_viewport",
                "--pyxel-runner",
                "pyxel-runner-that-does-not-exist",
            )

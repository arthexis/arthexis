"""Tests for the unified ``ocpp`` management command surface."""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase


class OcppCommandTests(SimpleTestCase):
    """Validate canonical ``manage.py ocpp ...`` command routes."""

    @patch("apps.ocpp.management.commands.ocpp.run_coverage_ocpp16")
    @patch("apps.ocpp.management.commands.ocpp.run_coverage_ocpp201")
    @patch("apps.ocpp.management.commands.ocpp.run_coverage_ocpp21")
    def test_coverage_routes_by_version(self, run_21, run_201, run_16) -> None:
        """Coverage versions should dispatch to the matching implementation."""

        call_command("ocpp", "coverage", "--version", "1.6J")
        run_16.assert_called_once()
        run_201.assert_not_called()
        run_21.assert_not_called()

        run_16.reset_mock()
        call_command("ocpp", "coverage", "--version", "2.0.1")
        run_16.assert_not_called()
        run_201.assert_called_once()
        run_21.assert_not_called()

        run_201.reset_mock()
        call_command("ocpp", "coverage", "--version", "2.1")
        run_16.assert_not_called()
        run_201.assert_not_called()
        run_21.assert_called_once()

    @patch("apps.ocpp.management.commands.ocpp.run_import_transactions", return_value=7)
    def test_transactions_import_runs_canonical_subcommand(self, run_import) -> None:
        """``ocpp transactions import`` should execute import and report count."""

        out = io.StringIO()
        call_command("ocpp", "transactions", "import", "/tmp/in.json", stdout=out)

        run_import.assert_called_once_with(input_path="/tmp/in.json")
        self.assertIn("Imported 7 transactions", out.getvalue())

    @patch("apps.ocpp.management.commands.ocpp.run_export_transactions", return_value=5)
    def test_transactions_export_runs_canonical_subcommand(self, run_export) -> None:
        """``ocpp transactions export`` should execute export with filter options."""

        out = io.StringIO()
        call_command(
            "ocpp",
            "transactions",
            "export",
            "/tmp/out.json",
            "--start",
            "2026-01-01",
            "--end",
            "2026-01-31",
            "--chargers",
            "CP-1",
            "CP-2",
            stdout=out,
        )

        run_export.assert_called_once_with(
            output_path="/tmp/out.json",
            start="2026-01-01",
            end="2026-01-31",
            chargers=["CP-1", "CP-2"],
        )
        self.assertIn("Exported 5 transactions", out.getvalue())

    @patch("apps.ocpp.management.commands.ocpp.run_trace_extract")
    def test_trace_extract_runs_canonical_subcommand(self, run_extract) -> None:
        """``ocpp trace extract`` should pass through extract arguments."""

        call_command("ocpp", "trace", "extract", "--all", "--next", "20", "--txn", "99")

        run_extract.assert_called_once()
        kwargs = run_extract.call_args.kwargs
        self.assertTrue(kwargs["all"])
        self.assertEqual(kwargs["next"], 20)
        self.assertEqual(kwargs["txn"], "99")

    @patch("apps.ocpp.management.commands.ocpp.run_replay_extract")
    def test_trace_replay_runs_canonical_subcommand(self, run_replay) -> None:
        """``ocpp trace replay`` should import and report replay results."""

        run_replay.return_value = SimpleNamespace(imported=3, skipped=1, session_log_written=True)
        out = io.StringIO()

        call_command("ocpp", "trace", "replay", "/tmp/extract.json", stdout=out)

        run_replay.assert_called_once_with(extract="/tmp/extract.json")
        rendered = out.getvalue()
        self.assertIn("Imported 3 transaction(s), skipped 1 duplicate(s).", rendered)
        self.assertIn("Session log restored.", rendered)

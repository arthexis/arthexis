"""Tests for the unified ``ocpp`` management command surface."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

from django.core.management import call_command, get_commands
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from apps.ocpp.management.ocpp_replay_impl import ReplayResult


class OcppCommandTests(SimpleTestCase):
    """Validate canonical OCPP command entrypoints and dispatch."""

    def test_legacy_alias_commands_are_not_registered(self) -> None:
        commands = get_commands()

        self.assertIn("ocpp", commands)
        self.assertNotIn("coverage_ocpp16", commands)
        self.assertNotIn("coverage_ocpp201", commands)
        self.assertNotIn("coverage_ocpp21", commands)
        self.assertNotIn("import_transactions", commands)
        self.assertNotIn("export_transactions", commands)
        self.assertNotIn("ocpp_replay", commands)

    def test_coverage_routes_to_selected_version_runner(self) -> None:
        run_16 = Mock()
        run_201 = Mock()
        run_21 = Mock()

        from apps.ocpp.management.commands import ocpp as ocpp_command

        original_16 = ocpp_command.run_coverage_ocpp16
        original_201 = ocpp_command.run_coverage_ocpp201
        original_21 = ocpp_command.run_coverage_ocpp21

        ocpp_command.run_coverage_ocpp16 = run_16
        ocpp_command.run_coverage_ocpp201 = run_201
        ocpp_command.run_coverage_ocpp21 = run_21
        self.addCleanup(setattr, ocpp_command, "run_coverage_ocpp16", original_16)
        self.addCleanup(setattr, ocpp_command, "run_coverage_ocpp201", original_201)
        self.addCleanup(setattr, ocpp_command, "run_coverage_ocpp21", original_21)

        call_command("ocpp", "coverage", "--version", "2.0.1")

        run_16.assert_not_called()
        run_21.assert_not_called()
        run_201.assert_called_once()

    def test_transactions_import_dispatches_through_canonical_entrypoint(self) -> None:
        from apps.ocpp.management.commands import ocpp as ocpp_command

        runner = Mock(return_value=3)
        original_runner = ocpp_command.run_import_transactions
        ocpp_command.run_import_transactions = runner
        self.addCleanup(setattr, ocpp_command, "run_import_transactions", original_runner)

        input_path = Path("/tmp/ocpp-import.json")
        input_path.write_text(json.dumps({"transactions": []}), encoding="utf-8")

        stdout = Mock()
        call_command("ocpp", "transactions", "import", str(input_path), stdout=stdout)

        runner.assert_called_once_with(input_path=str(input_path))

    def test_transactions_export_dispatches_through_canonical_entrypoint(self) -> None:
        from apps.ocpp.management.commands import ocpp as ocpp_command

        runner = Mock(return_value=4)
        original_runner = ocpp_command.run_export_transactions
        ocpp_command.run_export_transactions = runner
        self.addCleanup(setattr, ocpp_command, "run_export_transactions", original_runner)

        call_command(
            "ocpp",
            "transactions",
            "export",
            "out.json",
            "--start",
            "2025-01-01",
            "--end",
            "2025-01-02",
            "--chargers",
            "CP-1",
            "CP-2",
        )

        runner.assert_called_once_with(
            output_path="out.json",
            start="2025-01-01",
            end="2025-01-02",
            chargers=["CP-1", "CP-2"],
        )

    def test_trace_replay_dispatches_through_canonical_entrypoint(self) -> None:
        from apps.ocpp.management.commands import ocpp as ocpp_command

        runner = Mock(return_value=ReplayResult(imported=2, skipped=1, session_log_written=True))
        original_runner = ocpp_command.run_replay_extract
        ocpp_command.run_replay_extract = runner
        self.addCleanup(setattr, ocpp_command, "run_replay_extract", original_runner)

        call_command("ocpp", "trace", "replay", "extract.json")

        runner.assert_called_once_with(extract="extract.json")

    def test_unknown_group_raises_command_error(self) -> None:
        command = __import__("apps.ocpp.management.commands.ocpp", fromlist=["Command"]).Command()

        with self.assertRaises(CommandError):
            command.handle(group="unknown")

"""Unified OCPP management command with subcommands."""

from __future__ import annotations

import asyncio
import json

from django.core.management.base import BaseCommand, CommandError

from apps.ocpp.management.commands._ocpp_command_helpers import (
    add_coverage_arguments,
    add_trace_extract_arguments,
    add_trace_replay_arguments,
    add_transactions_export_arguments,
    add_transactions_import_arguments,
)
from apps.ocpp.management.commands._trace_extract_impl import run_trace_extract
from apps.ocpp.management.coverage_ocpp16_impl import run_coverage_ocpp16
from apps.ocpp.management.coverage_ocpp21_impl import run_coverage_ocpp21
from apps.ocpp.management.coverage_ocpp201_impl import run_coverage_ocpp201
from apps.ocpp.management.export_transactions_impl import run_export_transactions
from apps.ocpp.management.import_transactions_impl import run_import_transactions
from apps.ocpp.management.ocpp_replay_impl import run_replay_extract
from apps.ocpp.simulator import (
    AuthorizeScenario,
    OCPP16Simulator,
    SimulatorConfig,
    SimulatorError,
)


class Command(BaseCommand):
    help = "Unified OCPP operational command surface."

    def add_arguments(self, parser) -> None:
        subparsers = parser.add_subparsers(dest="group", required=True)

        coverage_parser = subparsers.add_parser("coverage", help="Coverage reporting.")
        add_coverage_arguments(coverage_parser)
        coverage_parser.add_argument(
            "--version",
            required=True,
            choices=("1.6J", "1.6", "2.0.1", "2.1"),
            help="OCPP protocol version (1.6J preferred; 1.6 alias supported).",
        )

        transactions_parser = subparsers.add_parser(
            "transactions", help="Import/export transaction data."
        )
        transactions_subparsers = transactions_parser.add_subparsers(
            dest="transactions_action", required=True
        )

        transactions_import_parser = transactions_subparsers.add_parser(
            "import", help="Import transactions."
        )
        add_transactions_import_arguments(transactions_import_parser)

        transactions_export_parser = transactions_subparsers.add_parser(
            "export", help="Export transactions."
        )
        add_transactions_export_arguments(transactions_export_parser)

        trace_parser = subparsers.add_parser(
            "trace", help="Trace extract/replay tools."
        )
        trace_subparsers = trace_parser.add_subparsers(
            dest="trace_action", required=True
        )

        trace_extract_parser = trace_subparsers.add_parser(
            "extract", help="Extract transaction trace."
        )
        add_trace_extract_arguments(trace_extract_parser)

        trace_replay_parser = trace_subparsers.add_parser(
            "replay", help="Replay extracted trace."
        )
        add_trace_replay_arguments(trace_replay_parser)

        simulator_parser = subparsers.add_parser(
            "simulator", help="Run a model-independent OCPP charge-point simulator."
        )
        simulator_subparsers = simulator_parser.add_subparsers(
            dest="simulator_action", required=True
        )
        authorize_parser = simulator_subparsers.add_parser(
            "authorize", help="Boot a simulated OCPP 1.6J charger and authorize an idTag."
        )
        authorize_parser.add_argument(
            "--url", required=True, help="CSMS WebSocket base URL (for example ws://host:9000)."
        )
        authorize_parser.add_argument("--charger", required=True, help="Simulated charger ID.")
        authorize_parser.add_argument("--id-tag", required=True, help="RFID/OCPP idTag to send.")
        authorize_parser.add_argument("--vendor", default="ArtHexis")
        authorize_parser.add_argument("--model", default="Gway Simulator")
        authorize_parser.add_argument("--timeout", type=float, default=30.0)

    def handle(self, *args, **options):
        group = options.get("group")
        if group == "coverage":
            self._handle_coverage(options)
            return
        if group == "transactions":
            self._handle_transactions(options)
            return
        if group == "trace":
            self._handle_trace(options)
            return
        if group == "simulator":
            self._handle_simulator(options)
            return
        raise CommandError(
            "A command group is required: coverage, transactions, trace, or simulator."
        )

    def _handle_coverage(self, options: dict) -> None:
        version = options["version"]
        kwargs = {
            "badge_path": options.get("badge_path"),
            "json_path": options.get("json_path"),
            "stdout": self.stdout,
            "stderr": self.stderr,
        }
        if version in {"1.6", "1.6J"}:
            run_coverage_ocpp16(**kwargs)
        elif version == "2.0.1":
            run_coverage_ocpp201(**kwargs)
        elif version == "2.1":
            run_coverage_ocpp21(**kwargs)
        else:
            raise CommandError(f"Unsupported coverage version: {version}")

    def _handle_transactions(self, options: dict) -> None:
        action = options.get("transactions_action")
        if action == "import":
            imported = run_import_transactions(input_path=options["input"])
            self.stdout.write(self.style.SUCCESS(f"Imported {imported} transactions"))
            return
        if action == "export":
            count = run_export_transactions(
                output_path=options["output"],
                start=options.get("start"),
                end=options.get("end"),
                chargers=options.get("chargers"),
                all_chargers=options.get("all_chargers", False),
            )
            self.stdout.write(self.style.SUCCESS(f"Exported {count} transactions"))
            return
        raise CommandError("transactions requires one action: import or export.")

    def _handle_trace(self, options: dict) -> None:
        action = options.get("trace_action")
        if action == "extract":
            run_trace_extract(
                stdout=self.stdout,
                stderr=self.stderr,
                style=self.style,
                **{k: options.get(k) for k in ("all", "next", "txn", "out", "log")},
            )
            return
        if action == "replay":
            result = run_replay_extract(extract=options["extract"])
            self.stdout.write(
                self.style.SUCCESS(
                    f"Imported {result.imported} transaction(s), skipped {result.skipped} duplicate(s)."
                )
            )
            if result.session_log_written:
                self.stdout.write(self.style.SUCCESS("Session log restored."))
            return
        raise CommandError("trace requires one action: extract or replay.")

    def _handle_simulator(self, options: dict) -> None:
        action = options.get("simulator_action")
        if action != "authorize":
            raise CommandError("simulator requires one action: authorize.")
        config = SimulatorConfig(
            url=options["url"],
            charger=options["charger"],
            vendor=options["vendor"],
            model=options["model"],
            timeout=options["timeout"],
        )
        scenario = AuthorizeScenario(options["id_tag"])
        try:
            result = asyncio.run(scenario.run(OCPP16Simulator(config)))
        except (SimulatorError, OSError, TimeoutError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result.to_dict(), sort_keys=True))

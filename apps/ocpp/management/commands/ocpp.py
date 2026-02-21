"""Unified OCPP management command with subcommands."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.ocpp.management.commands._ocpp_command_helpers import (
    add_coverage_arguments,
    add_trace_extract_arguments,
    add_trace_replay_arguments,
    add_transactions_export_arguments,
    add_transactions_import_arguments,
)
from apps.ocpp.management.commands.coverage_ocpp16 import run_coverage_ocpp16
from apps.ocpp.management.commands.coverage_ocpp201 import run_coverage_ocpp201
from apps.ocpp.management.commands.coverage_ocpp21 import run_coverage_ocpp21
from apps.ocpp.management.commands.export_transactions import run_export_transactions
from apps.ocpp.management.commands.import_transactions import run_import_transactions
from apps.ocpp.management.commands.ocpp_replay import run_replay_extract
from apps.ocpp.management.commands._trace_extract_impl import run_trace_extract


class Command(BaseCommand):
    help = "Unified OCPP operational command surface."

    def add_arguments(self, parser) -> None:
        subparsers = parser.add_subparsers(dest="group", required=True)

        coverage_parser = subparsers.add_parser("coverage", help="Coverage reporting.")
        add_coverage_arguments(coverage_parser)
        coverage_parser.add_argument(
            "--version",
            required=True,
            choices=("1.6", "2.0.1", "2.1"),
            help="OCPP protocol version.",
        )

        transactions_parser = subparsers.add_parser(
            "transactions", help="Import/export transaction data."
        )
        transactions_subparsers = transactions_parser.add_subparsers(dest="transactions_action", required=True)

        transactions_import_parser = transactions_subparsers.add_parser("import", help="Import transactions.")
        add_transactions_import_arguments(transactions_import_parser)

        transactions_export_parser = transactions_subparsers.add_parser("export", help="Export transactions.")
        add_transactions_export_arguments(transactions_export_parser)

        trace_parser = subparsers.add_parser("trace", help="Trace extract/replay tools.")
        trace_subparsers = trace_parser.add_subparsers(dest="trace_action", required=True)

        trace_extract_parser = trace_subparsers.add_parser("extract", help="Extract transaction trace.")
        add_trace_extract_arguments(trace_extract_parser)

        trace_replay_parser = trace_subparsers.add_parser("replay", help="Replay extracted trace.")
        add_trace_replay_arguments(trace_replay_parser)

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
        raise CommandError("A command group is required: coverage, transactions, or trace.")

    def _handle_coverage(self, options: dict) -> None:
        version = options["version"]
        kwargs = {
            "badge_path": options.get("badge_path"),
            "json_path": options.get("json_path"),
            "stdout": self.stdout,
            "stderr": self.stderr,
        }
        if version == "1.6":
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

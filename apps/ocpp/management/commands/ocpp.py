"""Unified OCPP management command with subcommands."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from django.conf import settings
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
from apps.ocpp.simulator import SimulatorConfig, SimulatorError
from apps.ocpp.simulator.worker import (
    DEFAULT_IDLE_TIMEOUT,
    active_sessions,
    error_path,
    max_instances,
    send_control,
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
            "simulator", help="Control persistent simulated OCPP charge points."
        )
        simulator_subparsers = simulator_parser.add_subparsers(
            dest="simulator_action", required=True
        )
        open_parser = simulator_subparsers.add_parser(
            "open", help="Open a persistent simulated charger connection."
        )
        open_parser.add_argument("--url", required=True)
        open_parser.add_argument("--charger", required=True)
        open_parser.add_argument("--vendor", default="ArtHexis")
        open_parser.add_argument("--model", default="Gway Simulator")
        open_parser.add_argument("--timeout", type=float, default=30.0)
        open_parser.add_argument(
            "--idle-timeout", type=float, default=DEFAULT_IDLE_TIMEOUT
        )
        open_parser.add_argument(
            "--allow-insecure-ws",
            action="store_true",
            help="Allow plaintext ws:// for trusted local test networks.",
        )
        for name, help_text in (
            ("authorize", "Authorize an RFID/idTag on an open simulator."),
            ("status", "Show an open simulator's status."),
            ("close", "Close an open simulator connection."),
        ):
            action_parser = simulator_subparsers.add_parser(name, help=help_text)
            action_parser.add_argument("--charger", required=True)
            if name == "authorize":
                action_parser.add_argument("--id-tag", required=True)

        worker_parser = simulator_subparsers.add_parser(
            "_worker", help=argparse.SUPPRESS
        )
        worker_parser.add_argument("--config", required=True)
        worker_parser.add_argument("--idle-timeout", type=float, required=True)

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
        try:
            if action == "_worker":
                self._run_simulator_worker(options)
                return
            if action == "open":
                self._open_simulator(options)
                return
            if action not in {"authorize", "status", "close"}:
                raise CommandError(
                    "simulator requires one action: open, authorize, status, or close."
                )
            request = {"action": action}
            if action == "authorize":
                request["id_tag"] = options["id_tag"]
            result = asyncio.run(send_control(options["charger"], request))
            self.stdout.write(json.dumps(result, sort_keys=True))
        except (ValueError, SimulatorError, OSError, TimeoutError) as exc:
            raise CommandError(str(exc)) from exc

    def _run_simulator_worker(self, options: dict) -> None:
        from apps.ocpp.simulator.worker import SimulatorWorker

        data = json.loads(options["config"])
        config = SimulatorConfig(**data)
        path = error_path(config.charger)
        path.unlink(missing_ok=True)
        try:
            asyncio.run(
                SimulatorWorker(config, idle_timeout=options["idle_timeout"]).run()
            )
        except Exception as exc:
            path.write_text(str(exc))
            raise CommandError(str(exc)) from exc
        finally:
            if not path.exists():
                path.unlink(missing_ok=True)

    def _open_simulator(self, options: dict) -> None:
        sessions = active_sessions()
        charger = options["charger"]
        if any(session.get("charger") == charger for session in sessions):
            raise CommandError(f"simulator {charger!r} is already open")
        limit = max_instances()
        if len(sessions) >= limit:
            raise CommandError(
                f"simulator limit reached ({limit}); "
                "set OCPP_SIMULATOR_MAX_INSTANCES to raise it"
            )
        config = SimulatorConfig(
            url=options["url"],
            charger=charger,
            vendor=options["vendor"],
            model=options["model"],
            timeout=options["timeout"],
            allow_insecure_ws=options["allow_insecure_ws"],
        )
        _ = config.endpoint  # validate URL/security policy before spawning the worker
        if options["idle_timeout"] <= 0:
            raise CommandError("idle_timeout must be greater than zero")
        failure_path = error_path(charger)
        failure_path.unlink(missing_ok=True)
        manage_path = Path(settings.BASE_DIR) / "manage.py"
        argv = [
            sys.executable,
            str(manage_path),
            "ocpp",
            "simulator",
            "_worker",
            "--config",
            json.dumps(config.__dict__),
            "--idle-timeout",
            str(options["idle_timeout"]),
        ]
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=os.environ.copy(),
            cwd=str(settings.BASE_DIR),
        )
        deadline = time.monotonic() + options["timeout"]
        from apps.ocpp.simulator.worker import session_path

        ready_path = session_path(charger)
        while not ready_path.exists():
            if failure_path.exists():
                message = failure_path.read_text().strip() or "simulator worker failed"
                failure_path.unlink(missing_ok=True)
                raise CommandError(message)
            if process.poll() is not None:
                raise CommandError("simulator worker exited before becoming ready")
            if time.monotonic() >= deadline:
                raise CommandError("simulator worker did not become ready before timeout")
            time.sleep(0.05)
        self.stdout.write(
            json.dumps(
                {
                    "charger": charger,
                    "open": True,
                    "idle_timeout": options["idle_timeout"],
                    "max_instances": limit,
                },
                sort_keys=True,
            )
        )

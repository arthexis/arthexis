import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.ocpp.services import transaction_export
from apps.ocpp.simulator import SimulatorConfig, SimulatorError
from apps.ocpp.simulator.worker import (
    DEFAULT_IDLE_TIMEOUT,
    active_sessions,
    max_instances,
    send_control,
    session_path,
)
from apps.ocpp.trace import TraceError, extract_trace, replay_trace


class Command(BaseCommand):
    help = "Unified OCPP tooling for coverage, transaction data, traces, and simulation."

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="ocpp_command", required=True)

        coverage_parser = subparsers.add_parser(
            "coverage", help="Show implemented OCPP action coverage."
        )
        coverage_parser.add_argument("coverage_args", nargs=argparse.REMAINDER)

        transactions_parser = subparsers.add_parser(
            "transactions", help="Import or export transaction/session data."
        )
        transaction_subparsers = transactions_parser.add_subparsers(
            dest="transaction_command", required=True
        )
        export_parser = transaction_subparsers.add_parser(
            "export", help="Export transaction/session data as JSON."
        )
        export_parser.add_argument("--charger", dest="charger_id")
        export_parser.add_argument("--output")
        export_parser.add_argument("--indent", type=int, default=2)
        export_parser.add_argument("--database", default="default")
        export_parser.add_argument(
            "--include-payment-logs", action="store_true", default=False
        )

        import_parser = transaction_subparsers.add_parser(
            "import", help="Import transaction/session data from JSON."
        )
        import_parser.add_argument("input")
        import_parser.add_argument("--database", default="default")
        import_parser.add_argument("--dry-run", action="store_true", default=False)

        trace_parser = subparsers.add_parser(
            "trace", help="Extract or replay OCPP trace data."
        )
        trace_subparsers = trace_parser.add_subparsers(
            dest="trace_command", required=True
        )
        trace_extract_parser = trace_subparsers.add_parser(
            "extract", help="Extract normalized OCPP trace data from logs."
        )
        trace_extract_parser.add_argument("input")
        trace_extract_parser.add_argument("--output")
        trace_extract_parser.add_argument("--indent", type=int, default=2)
        trace_extract_parser.add_argument("--charger")

        trace_replay_parser = trace_subparsers.add_parser(
            "replay", help="Replay normalized OCPP trace data into local state."
        )
        trace_replay_parser.add_argument("input")
        trace_replay_parser.add_argument("--charger")
        trace_replay_parser.add_argument("--database", default="default")
        trace_replay_parser.add_argument("--dry-run", action="store_true", default=False)
        trace_replay_parser.add_argument("--reset", action="store_true", default=False)

        simulator_parser = subparsers.add_parser(
            "simulator", help="Control persistent simulated OCPP charge points."
        )
        simulator_subparsers = simulator_parser.add_subparsers(
            dest="simulator_command", required=True
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
        command = options["ocpp_command"]
        if command == "coverage":
            self._run_coverage(options)
            return
        if command == "transactions":
            self._run_transactions(options)
            return
        if command == "trace":
            self._run_trace(options)
            return
        if command == "simulator":
            self._run_simulator(options)
            return
        raise CommandError(f"Unknown OCPP command: {command}")

    def _run_coverage(self, options):
        from .ocpp_coverage import Command as CoverageCommand

        coverage = CoverageCommand()
        coverage.stdout = self.stdout
        coverage.stderr = self.stderr
        coverage.handle(*options.get("coverage_args", []))

    def _run_transactions(self, options):
        command = options["transaction_command"]
        try:
            if command == "export":
                payload = transaction_export.export_transactions(
                    charger_id=options.get("charger_id"),
                    using=options["database"],
                    include_payment_logs=options["include_payment_logs"],
                )
                text = json.dumps(payload, indent=options["indent"], sort_keys=True)
                output = options.get("output")
                if output:
                    Path(output).write_text(text + "\n", encoding="utf-8")
                    self.stdout.write(output)
                else:
                    self.stdout.write(text)
                return

            if command == "import":
                payload = json.loads(Path(options["input"]).read_text(encoding="utf-8"))
                summary = transaction_export.import_transactions(
                    payload,
                    using=options["database"],
                    dry_run=options["dry_run"],
                )
                self.stdout.write(json.dumps(summary, sort_keys=True))
                return
        except (OSError, ValueError, transaction_export.TransactionTransferError) as exc:
            raise CommandError(str(exc)) from exc
        raise CommandError(f"Unknown transaction command: {command}")

    def _run_trace(self, options):
        command = options["trace_command"]
        try:
            if command == "extract":
                summary = extract_trace(
                    options["input"],
                    output_path=options.get("output"),
                    charger=options.get("charger"),
                    indent=options["indent"],
                )
            elif command == "replay":
                summary = replay_trace(
                    options["input"],
                    charger=options.get("charger"),
                    database=options["database"],
                    dry_run=options["dry_run"],
                    reset=options["reset"],
                )
            else:
                raise CommandError(f"Unknown trace command: {command}")
        except (OSError, ValueError, TraceError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(summary, sort_keys=True))

    def _run_simulator(self, options):
        command = options["simulator_command"]
        try:
            if command == "_worker":
                from apps.ocpp.simulator.worker import SimulatorWorker

                data = json.loads(options["config"])
                config = SimulatorConfig(**data)
                asyncio.run(
                    SimulatorWorker(config, idle_timeout=options["idle_timeout"]).run()
                )
                return
            if command == "open":
                self._open_simulator(options)
                return
            request = {"action": command}
            if command == "authorize":
                request["id_tag"] = options["id_tag"]
            result = asyncio.run(send_control(options["charger"], request))
            self.stdout.write(json.dumps(result, sort_keys=True))
        except (OSError, ValueError, SimulatorError) as exc:
            raise CommandError(str(exc)) from exc

    def _open_simulator(self, options):
        charger = options["charger"]
        if session_path(charger).exists():
            raise CommandError(f"simulator {charger!r} is already open")
        sessions = active_sessions()
        limit = max_instances()
        if len(sessions) >= limit:
            raise CommandError(
                f"simulator limit reached ({limit}); set OCPP_SIMULATOR_MAX_INSTANCES to raise it"
            )
        config = SimulatorConfig(
            url=options["url"],
            charger=charger,
            vendor=options["vendor"],
            model=options["model"],
            timeout=options["timeout"],
        )
        argv = [
            sys.executable,
            str(Path(sys.argv[0]).resolve()),
            "ocpp",
            "simulator",
            "_worker",
            "--config",
            json.dumps(config.__dict__),
            "--idle-timeout",
            str(options["idle_timeout"]),
        ]
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=os.environ.copy(),
        )
        deadline = asyncio.get_event_loop_policy().new_event_loop().time() + min(
            options["timeout"], 5.0
        )
        import time

        while not session_path(charger).exists():
            if time.monotonic() >= deadline:
                raise CommandError("simulator worker did not become ready")
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

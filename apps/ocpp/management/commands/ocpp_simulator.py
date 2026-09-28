"""Operate a persistent live OCPP 1.6J charge-point simulator."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.ocpp.simulator.network import LiveSimulatorConfig, LiveSimulatorError
from apps.ocpp.simulator.worker import (
    DEFAULT_IDLE_TIMEOUT,
    cleanup_session_artifacts,
    error_path,
    load_session,
    send_control,
    session_path,
)


class Command(BaseCommand):
    help = "Control a persistent live OCPP 1.6J simulated charge point."

    def add_arguments(self, parser) -> None:
        actions = parser.add_subparsers(dest="action", required=True)

        open_parser = actions.add_parser(
            "open", help="Open, boot, and retain a live charger connection."
        )
        self._add_start_arguments(open_parser, legacy_url=True)

        start_parser = actions.add_parser(
            "start",
            help="Start a charger session pointed at a CSMS endpoint.",
        )
        self._add_start_arguments(start_parser, legacy_url=False)

        scenario_parser = actions.add_parser(
            "authorize-scenario",
            help="Run the standard live authorization policy matrix.",
        )
        scenario_parser.add_argument("--charger", required=True)
        scenario_parser.add_argument(
            "--policy-context",
            required=True,
            choices=("open", "restricted"),
            help="Describe the remote charger's configured authorization mode.",
        )
        scenario_parser.add_argument("--known-authorized", required=True)
        scenario_parser.add_argument("--known-denied", required=True)
        scenario_parser.add_argument("--unknown", required=True)
        scenario_parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Emit the complete privacy-safe scenario result as JSON.",
        )

        replay_parser = actions.add_parser(
            "replay",
            help="Replay migrated OCPP 1.6 transaction history over the live connection.",
        )
        replay_parser.add_argument("source_path", nargs="?")
        replay_parser.add_argument("--charger")
        replay_parser.add_argument("--source")
        replay_parser.add_argument("--source-charger")
        replay_parser.add_argument(
            "--stream",
            choices=("transactions", "inbound"),
            default="transactions",
            help=(
                "Replay reconstructed transaction history or retained inbound "
                "charger-originated OCPP requests."
            ),
        )
        replay_parser.add_argument("--batch-size", type=int, default=250)
        replay_parser.add_argument(
            "--pacing",
            choices=("maximum", "fixed", "burst"),
            default="maximum",
        )
        replay_parser.add_argument("--interval-seconds", type=float, default=0.0)
        replay_parser.add_argument("--burst-size", type=int, default=100)
        replay_parser.add_argument("--burst-pause-seconds", type=float, default=0.0)
        replay_parser.add_argument("--reconnect-after", type=int)

        for name, help_text in (
            ("authorize", "Send Authorize on an existing live connection."),
            ("status", "Report the existing live simulator state."),
            ("reconnect", "Reconnect and BootNotification the same charger."),
            ("close", "Close the existing live simulator."),
        ):
            action_parser = actions.add_parser(name, help=help_text)
            action_parser.add_argument("--charger")
            if name == "authorize":
                action_parser.add_argument("id_tag_value", nargs="?")
                action_parser.add_argument("--id-tag")

        stop_parser = actions.add_parser(
            "stop", help="Stop the existing live simulator session."
        )
        stop_parser.add_argument("--charger")

        worker_parser = actions.add_parser("_worker", help=argparse.SUPPRESS)
        worker_parser.add_argument("--config", required=True)
        worker_parser.add_argument("--idle-timeout", type=float, required=True)

    def handle(self, *args, **options):
        action = options["action"]
        try:
            if action == "_worker":
                self._run_worker(options)
                return
            if action in {"open", "start"}:
                self._open(options)
                return

            charger = options.get("charger") or self._default_charger_identity()
            request_action = "close" if action == "stop" else action
            request = {"action": request_action}
            if action == "authorize":
                id_tag = options.get("id_tag") or options.get("id_tag_value")
                if not id_tag:
                    raise CommandError("authorize requires an idTag")
                request["id_tag"] = id_tag
            elif action == "authorize-scenario":
                request.update(
                    {
                        "policy_context": options["policy_context"],
                        "known_authorized": options["known_authorized"],
                        "known_denied": options["known_denied"],
                        "unknown": options["unknown"],
                    }
                )
            elif action == "replay":
                source = options.get("source") or options.get("source_path")
                if not source:
                    raise CommandError("replay requires a migrated database or package")
                request.update(
                    {
                        "source": source,
                        "source_charger": options["source_charger"],
                        "stream": options["stream"],
                        "batch_size": options["batch_size"],
                        "pacing": options["pacing"],
                        "interval_seconds": options["interval_seconds"],
                        "burst_size": options["burst_size"],
                        "burst_pause_seconds": options["burst_pause_seconds"],
                        "reconnect_after": options["reconnect_after"],
                    }
                )
            result = asyncio.run(send_control(charger, request))
            if action == "authorize-scenario" and not options["json_output"]:
                self.stdout.write(self._format_authorization_scenario(result))
            else:
                self.stdout.write(json.dumps(result, sort_keys=True))
        except (LiveSimulatorError, OSError, ValueError, TimeoutError) as exc:
            raise CommandError(str(exc)) from exc

    @staticmethod
    def _default_charger_identity() -> str:
        configured = os.environ.get("ARTHEXIS_OCPP_SIMULATOR_IDENTITY", "").strip()
        if configured:
            return configured
        hostname = socket.gethostname().split(".", 1)[0].strip()
        if not hostname:
            raise CommandError(
                "cannot derive simulator charger identity; pass --charger or "
                "set ARTHEXIS_OCPP_SIMULATOR_IDENTITY"
            )
        return hostname

    @staticmethod
    def _allow_local_insecure_ws(endpoint: str) -> bool:
        parsed = urlsplit(endpoint)
        if parsed.scheme.lower() != "ws" or not parsed.hostname:
            return False
        try:
            host = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            return False
        return host.is_private or host.is_loopback or host.is_link_local

    @staticmethod
    def _add_start_arguments(parser, *, legacy_url: bool) -> None:
        if legacy_url:
            parser.add_argument("--url", required=True)
        else:
            parser.add_argument("endpoint")
            parser.add_argument("--url", help=argparse.SUPPRESS)
        parser.add_argument("--charger")
        parser.add_argument("--vendor", default="Arthexis")
        parser.add_argument("--model", default="Gway Simulator")
        parser.add_argument("--timeout", type=float, default=30.0)
        parser.add_argument(
            "--idle-timeout", type=float, default=DEFAULT_IDLE_TIMEOUT
        )
        parser.add_argument(
            "--allow-insecure-ws",
            action="store_true",
            help=(
                "Allow plaintext ws:// to a non-local endpoint. Private, loopback, "
                "and link-local IP endpoints are allowed automatically for field tests."
            ),
        )

    @staticmethod
    def _format_authorization_scenario(result: dict[str, object]) -> str:
        lines = [
            f"Authorization scenario: {result.get('scenario', '-')}",
            f"Charger: {result.get('charger', '-')}",
            f"Policy context: {result.get('policy_context', '-')}",
        ]
        entries = result.get("results", [])
        if not isinstance(entries, list):
            raise LiveSimulatorError("authorization scenario returned invalid results")
        for entry in entries:
            if not isinstance(entry, dict):
                raise LiveSimulatorError("authorization scenario returned invalid result")
            sequence = entry.get("sequence", "?")
            attempt = entry.get("attempt", "-")
            outcome = entry.get("status") or f"ERROR: {entry.get('error', 'unknown')}"
            repeat_of = entry.get("repeat_of")
            suffix = f" (repeat of {repeat_of})" if repeat_of else ""
            lines.append(f"{sequence}. {attempt}: {outcome}{suffix}")
        return "\n".join(lines)

    def _run_worker(self, options) -> None:
        from apps.ocpp.simulator.worker import LiveSimulatorWorker

        config = LiveSimulatorConfig(**json.loads(options["config"]))
        failure = error_path(config.charger)
        failure.unlink(missing_ok=True)
        try:
            asyncio.run(
                LiveSimulatorWorker(
                    config=config,
                    idle_timeout=options["idle_timeout"],
                ).run()
            )
        except Exception as exc:
            failure.write_text(str(exc))
            raise CommandError(str(exc)) from exc

    def _open(self, options) -> None:
        charger = options["charger"]
        try:
            load_session(charger)
        except LiveSimulatorError:
            pass
        else:
            raise CommandError(f"simulator {charger!r} is already open")

        if options["idle_timeout"] <= 0:
            raise CommandError("--idle-timeout must be greater than zero")

        config = LiveSimulatorConfig(
            url=options["url"],
            charger=charger,
            vendor=options["vendor"],
            model=options["model"],
            timeout=options["timeout"],
            allow_insecure_ws=options["allow_insecure_ws"],
        )
        _ = config.endpoint

        cleanup_session_artifacts(charger)
        manage_path = Path(settings.BASE_DIR) / "manage.py"
        process = subprocess.Popen(
            [
                sys.executable,
                str(manage_path),
                "ocpp_simulator",
                "_worker",
                "--config",
                json.dumps(config.__dict__),
                "--idle-timeout",
                str(options["idle_timeout"]),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(settings.BASE_DIR),
            env=os.environ.copy(),
        )

        deadline = time.monotonic() + options["timeout"]
        ready = session_path(charger)
        failure = error_path(charger)
        while not ready.exists():
            if failure.exists():
                message = failure.read_text().strip() or "simulator worker failed"
                self._stop_starting_worker(process, charger)
                raise CommandError(message)
            if process.poll() is not None:
                self._stop_starting_worker(process, charger)
                raise CommandError("simulator worker exited before becoming ready")
            if time.monotonic() >= deadline:
                self._stop_starting_worker(process, charger)
                raise CommandError(
                    "simulator worker did not become ready before timeout"
                )
            time.sleep(0.05)

        metadata = load_session(charger)
        self.stdout.write(
            json.dumps(
                {
                    "charger": charger,
                    "open": True,
                    "boot": metadata.get("boot"),
                    "idle_timeout": options["idle_timeout"],
                },
                sort_keys=True,
            )
        )

    @staticmethod
    def _stop_starting_worker(process: subprocess.Popen, charger: str) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        cleanup_session_artifacts(charger)

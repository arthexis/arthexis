"""Operate a persistent live OCPP 1.6J charge-point simulator."""

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
        open_parser.add_argument("--url", required=True)
        open_parser.add_argument("--charger", required=True)
        open_parser.add_argument("--vendor", default="Arthexis")
        open_parser.add_argument("--model", default="Gway Simulator")
        open_parser.add_argument("--timeout", type=float, default=30.0)
        open_parser.add_argument(
            "--idle-timeout", type=float, default=DEFAULT_IDLE_TIMEOUT
        )
        open_parser.add_argument(
            "--allow-insecure-ws",
            action="store_true",
            help="Allow ws:// only for a trusted local test network.",
        )

        for name, help_text in (
            ("authorize", "Send Authorize on an existing live connection."),
            ("status", "Report the existing live simulator state."),
            ("reconnect", "Reconnect and BootNotification the same charger."),
            ("close", "Close the existing live simulator."),
        ):
            action_parser = actions.add_parser(name, help=help_text)
            action_parser.add_argument("--charger", required=True)
            if name == "authorize":
                action_parser.add_argument("--id-tag", required=True)

        worker_parser = actions.add_parser("_worker", help=argparse.SUPPRESS)
        worker_parser.add_argument("--config", required=True)
        worker_parser.add_argument("--idle-timeout", type=float, required=True)

    def handle(self, *args, **options):
        action = options["action"]
        try:
            if action == "_worker":
                self._run_worker(options)
                return
            if action == "open":
                self._open(options)
                return

            request = {"action": action}
            if action == "authorize":
                request["id_tag"] = options["id_tag"]
            result = asyncio.run(send_control(options["charger"], request))
            self.stdout.write(json.dumps(result, sort_keys=True))
        except (LiveSimulatorError, OSError, ValueError, TimeoutError) as exc:
            raise CommandError(str(exc)) from exc

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

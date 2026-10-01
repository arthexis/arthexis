"""Operate a persistent live OCPP 1.6J charge-point simulator."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import socket
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.ocpp.simulator.evidence import create_session_evidence
from apps.ocpp.simulator.network import LiveSimulatorConfig, LiveSimulatorError
from apps.ocpp.simulator.profile import (
    ChargerProfile,
    JsonChargerProfileSource,
    load_profile,
)
from apps.ocpp.simulator.service import (
    GwaySimulatorServiceController,
    SimulatorService,
    send_service_control,
)
from apps.ocpp.simulator.worker import (
    DEFAULT_IDLE_TIMEOUT,
    active_session,
    cleanup_session_artifacts,
    error_path,
    send_control,
)


class Command(BaseCommand):
    help = "Control a persistent live OCPP 1.6J simulated charge point."

    def add_arguments(self, parser) -> None:
        actions = parser.add_subparsers(dest="action", required=True)

        boot_parser = actions.add_parser(
            "boot",
            help="Start on demand, boot, and retain one live charger connection.",
        )
        self._add_start_arguments(boot_parser, legacy_url=False)

        open_parser = actions.add_parser(
            "open", help="Compatibility alias for boot using --url."
        )
        self._add_start_arguments(open_parser, legacy_url=True)

        start_parser = actions.add_parser(
            "start",
            help="Compatibility alias for boot.",
        )
        self._add_start_arguments(start_parser, legacy_url=False)

        scenario_parser = actions.add_parser(
            "authorize-scenario",
            help="Run the standard live authorization policy matrix.",
        )
        scenario_parser.add_argument("--charger")
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
            ("authorize", "Submit Authorize on the existing live connection."),
            ("status", "Report the existing live simulator state."),
            ("reconnect", "Reconnect and BootNotification the same charger."),
            ("close", "Close the existing live simulator."),
        ):
            action_parser = actions.add_parser(name, help=help_text)
            action_parser.add_argument("--charger")
            if name == "authorize":
                action_parser.add_argument("id_tag_value", nargs="?")
                action_parser.add_argument("--id-tag")
                action_parser.add_argument(
                    "--wait",
                    action="store_true",
                    help="Wait for the correlated Authorize result.",
                )
                action_parser.add_argument(
                    "--timeout",
                    type=float,
                    help=(
                        "Maximum seconds to wait. Defaults to the active charger "
                        "profile authorization timeout."
                    ),
                )

        stop_parser = actions.add_parser(
            "stop", help="Stop the existing live simulator session and service."
        )
        stop_parser.add_argument("--charger")

        worker_parser = actions.add_parser("_worker", help=argparse.SUPPRESS)
        worker_parser.add_argument("--config", required=True)
        worker_parser.add_argument("--idle-timeout", type=float, required=True)
        actions.add_parser("_service", help=argparse.SUPPRESS)

    def handle(self, *args, **options):
        action = options["action"]
        try:
            if action == "_worker":
                self._run_worker(options)
                return
            if action == "_service":
                asyncio.run(SimulatorService().run())
                return
            if action in {"boot", "open", "start"}:
                self._open(options)
                return
            if action == "stop":
                self._stop(options)
                return

            charger = self._resolve_active_charger(options.get("charger"))
            request = {"action": action}
            if action == "authorize":
                id_tag = options.get("id_tag") or options.get("id_tag_value")
                if not id_tag:
                    raise CommandError("authorize requires an idTag")
                if options.get("timeout") is not None and not options.get("wait"):
                    raise CommandError("--timeout requires --wait")
                if options.get("timeout") is not None and options["timeout"] < 0:
                    raise CommandError("--timeout must be zero or greater")
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
            if action == "authorize" and options.get("wait"):
                request_id = result.get("request_id")
                if not request_id:
                    raise LiveSimulatorError(
                        "simulator did not return an authorization request_id"
                    )
                timeout = options.get("timeout")
                if timeout is None:
                    current = active_session() or {}
                    configured_timeout = current.get("authorization_timeout")
                    if configured_timeout is not None:
                        timeout = float(configured_timeout)
                wait_request = {
                    "action": "wait-result",
                    "request_id": request_id,
                }
                if timeout is not None:
                    wait_request["timeout"] = timeout
                result = asyncio.run(send_control(charger, wait_request))
                result.setdefault("submitted", True)
            if action == "authorize-scenario" and not options["json_output"]:
                self.stdout.write(self._format_authorization_scenario(result))
            else:
                self.stdout.write(json.dumps(result, sort_keys=True))
        except (LiveSimulatorError, OSError, ValueError, TimeoutError) as exc:
            raise CommandError(str(exc)) from exc

    @classmethod
    def _resolve_active_charger(cls, requested: str | None) -> str:
        """Resolve commands to the one active simulator without repeated identity args."""
        current = active_session()
        if current is not None:
            active_charger = str(current["charger"])
            if requested and requested != active_charger:
                raise CommandError(
                    f"simulator is active as {active_charger!r}; stop it before "
                    f"targeting {requested!r}"
                )
            return active_charger
        return requested or cls._default_charger_identity()

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
            parser.add_argument("endpoint", nargs="?")
            parser.add_argument("--url", help=argparse.SUPPRESS)
        parser.add_argument("--profile")
        parser.add_argument("--charger")
        parser.add_argument("--protocol", choices=("ocpp1.6", "ocpp1.6j"))
        parser.add_argument("--vendor")
        parser.add_argument("--model")
        parser.add_argument("--serial")
        parser.add_argument("--firmware-version")
        parser.add_argument("--timeout", type=float)
        parser.add_argument("--authorization-timeout", type=float)
        parser.add_argument(
            "--clock-mode",
            choices=("host", "offset", "fixed", "frozen", "advancing"),
        )
        parser.add_argument("--clock-offset-seconds", type=float)
        parser.add_argument("--clock-start-time")
        parser.set_defaults(heartbeat=None, reconnect=None)
        parser.add_argument("--heartbeat", action="store_true", dest="heartbeat")
        parser.add_argument("--no-heartbeat", action="store_false", dest="heartbeat")
        parser.add_argument("--reconnect", action="store_true", dest="reconnect")
        parser.add_argument("--no-reconnect", action="store_false", dest="reconnect")
        parser.add_argument(
            "--idle-timeout",
            type=float,
            default=DEFAULT_IDLE_TIMEOUT,
            help=(
                "Optional positive inactivity timeout. Zero keeps the on-demand "
                "simulator alive until explicit stop."
            ),
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
        """Compatibility entry point for pre-service direct worker launches."""
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

    @staticmethod
    def _profile_overrides(options, endpoint: str | None) -> dict[str, object]:
        overrides: dict[str, object] = {}
        if options.get("charger"):
            overrides["identity"] = options["charger"]
        if options.get("protocol"):
            overrides["protocol"] = options["protocol"]

        target: dict[str, object] = {}
        if endpoint:
            target["url"] = endpoint
        if options.get("timeout") is not None:
            target["timeout_seconds"] = options["timeout"]
        if options.get("allow_insecure_ws"):
            target["allow_insecure_ws"] = True
        if target:
            overrides["target"] = target

        boot: dict[str, object] = {}
        for option, key in (
            ("vendor", "vendor"),
            ("model", "model"),
            ("serial", "serial"),
            ("firmware_version", "firmware_version"),
        ):
            if options.get(option) is not None:
                boot[key] = options[option]
        if boot:
            overrides["boot"] = boot

        behavior: dict[str, object] = {}
        if options.get("authorization_timeout") is not None:
            behavior["authorization_timeout_seconds"] = options[
                "authorization_timeout"
            ]
        if options.get("heartbeat") is not None:
            behavior["heartbeat"] = options["heartbeat"]
        if options.get("reconnect") is not None:
            behavior["reconnect"] = options["reconnect"]
        if behavior:
            overrides["behavior"] = behavior

        clock: dict[str, object] = {}
        if options.get("clock_mode") is not None:
            clock["mode"] = options["clock_mode"]
        if options.get("clock_offset_seconds") is not None:
            clock["offset_seconds"] = options["clock_offset_seconds"]
        if options.get("clock_start_time") is not None:
            clock["start_time"] = options["clock_start_time"]
        if clock:
            overrides["clock"] = clock
        return overrides

    def _open(self, options) -> None:
        endpoint_override = options.get("endpoint") or options.get("url")
        source = None
        source_payload: dict[str, object] = {}
        if options.get("profile"):
            source = JsonChargerProfileSource(Path(options["profile"]))
            source_payload = source.load()

        profile = load_profile(
            source,
            base={"identity": self._default_charger_identity()},
            overrides=self._profile_overrides(options, endpoint_override),
        )
        endpoint = str(profile.target.get("url") or "").strip()
        if not endpoint:
            raise CommandError("boot requires a CSMS endpoint or profile target.url")

        current = active_session()
        if current is not None:
            active_charger = str(current.get("charger"))
            active_endpoint = str(current.get("url"))
            if active_charger == profile.identity and active_endpoint == endpoint:
                raise CommandError(f"simulator {profile.identity!r} is already booted")
            raise CommandError(
                f"simulator is already active as {active_charger!r} at "
                f"{active_endpoint!r}; stop it before booting another charger"
            )

        if options["idle_timeout"] < 0:
            raise CommandError("--idle-timeout must be zero or greater")

        effective_mapping = profile.as_dict()
        if self._allow_local_insecure_ws(endpoint):
            effective_mapping["target"]["allow_insecure_ws"] = True
            profile = ChargerProfile.from_mapping(effective_mapping)

        if not source_payload:
            source_payload = profile.as_dict()
        evidence = create_session_evidence(
            profile.identity,
            source_profile=source_payload,
            effective_profile=profile.as_dict(),
        )

        config = LiveSimulatorConfig(
            url=endpoint,
            charger=profile.identity,
            vendor=str(profile.boot.get("vendor") or "Arthexis"),
            model=str(profile.boot.get("model") or "Gway Simulator"),
            serial=(
                str(profile.boot["serial"]) if profile.boot.get("serial") else None
            ),
            firmware_version=(
                str(profile.boot["firmware_version"])
                if profile.boot.get("firmware_version")
                else None
            ),
            timeout=float(profile.target["timeout_seconds"]),
            allow_insecure_ws=bool(profile.target["allow_insecure_ws"]),
            protocol=profile.protocol,
            authorization_timeout=float(
                profile.behavior["authorization_timeout_seconds"]
            ),
            heartbeat=bool(profile.behavior["heartbeat"]),
            reconnect_enabled=bool(profile.behavior["reconnect"]),
            clock=dict(profile.clock),
            evidence_dir=str(evidence),
        )
        _ = config.endpoint

        cleanup_session_artifacts(profile.identity)
        controller = self._service_controller()
        try:
            controller.ensure_started()
            result = asyncio.run(
                send_service_control(
                    {
                        "action": "boot",
                        "config": config.__dict__,
                        "idle_timeout": options["idle_timeout"],
                    }
                )
            )
        except Exception:
            controller.stop()
            cleanup_session_artifacts(profile.identity)
            raise

        result.update(
            {
                "profile": str(evidence / "profile.json"),
                "effective_profile": str(evidence / "effective-profile.json"),
            }
        )
        self.stdout.write(json.dumps(result, sort_keys=True))

    def _stop(self, options) -> None:
        controller = self._service_controller()
        requested = options.get("charger")
        try:
            result = asyncio.run(
                send_service_control({"action": "stop", "charger": requested})
            )
        except LiveSimulatorError as service_error:
            current = active_session()
            if current is None:
                controller.stop()
                raise service_error
            charger = self._resolve_active_charger(requested)
            result = asyncio.run(send_control(charger, {"action": "close"}))
        finally:
            controller.stop()
        self.stdout.write(json.dumps(result, sort_keys=True))

    @staticmethod
    def _service_controller() -> GwaySimulatorServiceController:
        return GwaySimulatorServiceController(
            manage_path=Path(settings.BASE_DIR) / "manage.py",
        )

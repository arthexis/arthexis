"""Run OCPP backlog load tests from synthetic or replayed data."""

import json
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from time import perf_counter

from asgiref.sync import async_to_sync
from django.core.management.base import BaseCommand, CommandError

from apps.ocpp.models import Charger
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.simulator import (
    OcppSimulator,
    ReplayPacing,
    run_v16_database_replay,
    run_v16_historical_backlog,
)
from apps.ocpp.simulator.sources import resolve_replay_database


class Command(BaseCommand):
    help = "Exercise historical OCPP backlog handling and measure live-request health."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--charger", required=True, help="Target charger identity.")
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--synthetic", action="store_true")
        source.add_argument("--database", type=Path, help="Bare DB or replay package path.")
        parser.add_argument("--messages", type=int, default=1000)
        parser.add_argument("--source-charger")
        parser.add_argument("--batch-size", type=int, default=250)
        parser.add_argument(
            "--pace",
            choices=("maximum", "fixed", "burst"),
            default="maximum",
        )
        parser.add_argument("--interval", type=float, default=0.0)
        parser.add_argument("--burst-size", type=int, default=100)
        parser.add_argument("--burst-pause", type=float, default=0.0)
        parser.add_argument(
            "--live-probe",
            choices=("Heartbeat", "Authorize", "StatusNotification"),
            default="Heartbeat",
        )
        parser.add_argument("--live-every", type=int, default=100)
        parser.add_argument(
            "--reconnect-after",
            type=int,
            help="Reconnect the backlog client after this many completed backlog events.",
        )
        parser.add_argument(
            "--max-live-latency",
            type=float,
            default=0.5,
            help="Maximum acceptable live-probe latency in seconds.",
        )
        parser.add_argument("--json", action="store_true", dest="json_output")

    def handle(self, *args, **options):
        try:
            charger = Charger.objects.get_by_natural_key(options["charger"])
        except Charger.DoesNotExist as error:
            raise CommandError(f"Unknown charger: {options['charger']}") from error
        if options["live_every"] < 1:
            raise CommandError("--live-every must be positive")
        if options["max_live_latency"] <= 0:
            raise CommandError("--max-live-latency must be positive")
        if options["reconnect_after"] is not None and options["reconnect_after"] < 1:
            raise CommandError("--reconnect-after must be positive")

        try:
            if options["synthetic"]:
                payload = self._run_synthetic(charger, options)
            else:
                payload = self._run_database(charger, options)
        except ValueError as error:
            raise CommandError(str(error)) from error

        live_latency_ok = (
            payload.get("live_failures", 0) == 0
            and payload.get("max_live_latency_seconds", 0.0)
            <= options["max_live_latency"]
        )
        payload["max_live_latency_threshold_seconds"] = options["max_live_latency"]
        payload["live_latency_ok"] = live_latency_ok

        if options["json_output"]:
            self.stdout.write(json.dumps(payload, sort_keys=True))
        else:
            for key, value in payload.items():
                self.stdout.write(f"{key}: {value}")
        if not live_latency_ok:
            raise CommandError("Live OCPP health threshold failed during backlog load.")

    def _run_synthetic(self, charger: Charger, options) -> dict[str, object]:
        cutover = charger.authority_cutover_at
        if cutover is None:
            raise ValueError("Synthetic historical load requires charger authority cutover.")
        result = async_to_sync(run_v16_historical_backlog)(
            charger,
            meter_values=options["messages"],
            start_at=cutover - timedelta(days=1),
            live_every=options["live_every"],
            live_action=options["live_probe"],
            reconnect_after=options["reconnect_after"],
        )
        return {"source": "synthetic", **asdict(result)}

    def _run_database(self, charger: Charger, options) -> dict[str, object]:
        resolved = resolve_replay_database(options["database"])
        pacing = ReplayPacing(
            mode=options["pace"],
            interval_seconds=options["interval"],
            burst_size=options["burst_size"],
            burst_pause_seconds=options["burst_pause"],
        )
        live_client = OcppSimulator(charger=charger, version=ProtocolVersion.OCPP_16)
        live_latencies: list[float] = []
        live_failures = 0

        async def after_event(count: int) -> None:
            nonlocal live_failures
            if count % options["live_every"]:
                return
            started = perf_counter()
            try:
                await live_client.call(
                    options["live_probe"],
                    _live_payload(options["live_probe"]),
                )
            except Exception:
                live_failures += 1
                raise
            finally:
                live_latencies.append(max(0.0, perf_counter() - started))

        started = perf_counter()
        completed = async_to_sync(run_v16_database_replay)(
            charger,
            resolved.database,
            source_charger_identity=options["source_charger"],
            batch_size=options["batch_size"],
            pacing=pacing,
            reconnect_after=options["reconnect_after"],
            after_event=after_event,
        )
        elapsed = max(0.0, perf_counter() - started)
        return {
            "source": resolved.kind,
            "database": str(resolved.database),
            "capture_id": resolved.capture_id,
            "backlog_events": len(completed),
            "elapsed_seconds": elapsed,
            "throughput_per_second": len(completed) / elapsed if elapsed else 0.0,
            "live_probes": len(live_latencies),
            "live_failures": live_failures,
            "mean_live_latency_seconds": (
                sum(live_latencies) / len(live_latencies) if live_latencies else 0.0
            ),
            "max_live_latency_seconds": max(live_latencies, default=0.0),
            "reconnects": int(
                options["reconnect_after"] is not None
                and len(completed) > options["reconnect_after"]
            ),
        }


def _live_payload(action: str) -> dict[str, object]:
    if action == "Heartbeat":
        return {}
    if action == "Authorize":
        return {"idTag": "live-load-probe"}
    if action == "StatusNotification":
        return {"connectorId": 1, "status": "Available"}
    raise ValueError(f"Unsupported live probe action: {action}")

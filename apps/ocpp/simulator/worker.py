"""Persistent local control worker for one live simulated charger."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from apps.ocpp.simulator.authorization import (
    authorization_policy_scenario,
    run_live_authorization_scenario,
)
from apps.ocpp.simulator.database_replay import (
    ReplayMetrics,
    ReplayPacing,
    iter_v16_inbound_request_replay,
    iter_v16_transaction_replay,
    run_v16_live_replay_events,
)
from apps.ocpp.simulator.sources import resolve_replay_database
from apps.ocpp.simulator.network import (
    LiveOcpp16Simulator,
    LiveSimulatorConfig,
    LiveSimulatorError,
)

DEFAULT_IDLE_TIMEOUT = 300.0
MAX_REPLAY_ACTIONS_IN_RESPONSE = 1000


def runtime_dir() -> Path:
    root = Path(
        os.environ.get("OCPP_SIMULATOR_RUNTIME_DIR", ".arthexis/ocpp-simulators")
    )
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root


def _safe_name(charger: str) -> str:
    digest = base64.urlsafe_b64encode(
        hashlib.sha256(charger.encode("utf-8")).digest()
    ).decode("ascii")
    return f"charger-{digest.rstrip('=')}"


def session_path(charger: str) -> Path:
    return runtime_dir() / f"{_safe_name(charger)}.json"


def socket_path(charger: str) -> Path:
    return runtime_dir() / f"{_safe_name(charger)}.sock"


def error_path(charger: str) -> Path:
    return runtime_dir() / f"{_safe_name(charger)}.error"


def cleanup_session_artifacts(charger: str) -> None:
    session_path(charger).unlink(missing_ok=True)
    socket_path(charger).unlink(missing_ok=True)
    error_path(charger).unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def load_session(charger: str) -> dict[str, Any]:
    path = session_path(charger)
    if not path.exists():
        raise LiveSimulatorError(f"simulator {charger!r} is not open")
    try:
        metadata = json.loads(path.read_text())
        pid = int(metadata["pid"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        cleanup_session_artifacts(charger)
        raise LiveSimulatorError(
            f"simulator {charger!r} has invalid session metadata"
        ) from exc
    if not _pid_alive(pid):
        cleanup_session_artifacts(charger)
        raise LiveSimulatorError(f"simulator {charger!r} is not open")
    return metadata


async def send_control(charger: str, request: dict[str, Any]) -> dict[str, Any]:
    metadata = load_session(charger)
    try:
        reader, writer = await asyncio.open_unix_connection(metadata["socket"])
    except (OSError, KeyError) as exc:
        cleanup_session_artifacts(charger)
        raise LiveSimulatorError(f"cannot contact simulator {charger!r}") from exc
    try:
        writer.write((json.dumps(request) + "\n").encode())
        await writer.drain()
        raw = await reader.readline()
    finally:
        writer.close()
        await writer.wait_closed()
    if not raw:
        raise LiveSimulatorError("simulator worker closed without a response")
    response = json.loads(raw)
    if not response.get("ok"):
        raise LiveSimulatorError(
            str(response.get("error", "simulator command failed"))
        )
    return response


@dataclass
class LiveSimulatorWorker:
    config: LiveSimulatorConfig
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT
    simulator_factory: Callable[[LiveSimulatorConfig], LiveOcpp16Simulator] = field(
        default=LiveOcpp16Simulator,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.idle_timeout <= 0:
            raise ValueError("idle_timeout must be greater than zero")
        self._last_control_activity = time.monotonic()
        self._stop = asyncio.Event()
        self._simulator = self.simulator_factory(self.config)
        self._boot = None
        self._reconnects = 0
        self._transport_lock = asyncio.Lock()

    async def run(self) -> None:
        sock = socket_path(self.config.charger)
        metadata_path = session_path(self.config.charger)
        sock.unlink(missing_ok=True)
        server = None
        idle_task = None
        heartbeat_task = None
        await self.connect_and_boot()
        try:
            server = await asyncio.start_unix_server(
                self._handle_client,
                path=str(sock),
                start_serving=False,
            )
            sock.chmod(0o600)
            await server.start_serving()
            metadata_path.write_text(
                json.dumps(
                    {
                        "charger": self.config.charger,
                        "url": self.config.url,
                        "pid": os.getpid(),
                        "socket": str(sock),
                        "idle_timeout": self.idle_timeout,
                        "boot": self._boot.status,
                    }
                )
            )
            self._last_control_activity = time.monotonic()
            idle_task = asyncio.create_task(self._idle_watch())
            heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            async with server:
                await self._stop.wait()
        finally:
            for task in (idle_task, heartbeat_task):
                if task is not None:
                    task.cancel()
            for task in (idle_task, heartbeat_task):
                if task is not None:
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            if server is not None:
                server.close()
                await server.wait_closed()
            await self._simulator.close()
            cleanup_session_artifacts(self.config.charger)

    async def connect_and_boot(self) -> None:
        """Connect the charger transport and require an accepted boot."""
        async with self._transport_lock:
            await self._simulator.connect()
            self._boot = await self._simulator.boot()
        if self._boot.status != "Accepted":
            await self._simulator.close()
            raise LiveSimulatorError(
                f"BootNotification was not accepted: {self._boot.status}"
            )

    async def reconnect(self) -> None:
        """Replace the charger transport and require an accepted re-boot."""
        async with self._transport_lock:
            await self._simulator.reconnect()
            self._boot = await self._simulator.boot()
        if self._boot.status != "Accepted":
            raise LiveSimulatorError(
                f"BootNotification was not accepted after reconnect: "
                f"{self._boot.status}"
            )
        self._reconnects += 1

    async def _idle_watch(self) -> None:
        while not self._stop.is_set():
            remaining = self.idle_timeout - (
                time.monotonic() - self._last_control_activity
            )
            if remaining <= 0:
                self._stop.set()
                return
            await asyncio.sleep(min(remaining, 1.0))

    async def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            interval = getattr(self._boot, "interval", 0)
            if not interval:
                return
            await asyncio.sleep(interval)
            if self._stop.is_set():
                return
            try:
                await self.heartbeat()
            except LiveSimulatorError:
                self._stop.set()
                return

    async def _handle_client(self, reader, writer) -> None:
        try:
            raw = await reader.readline()
            request = json.loads(raw)
            self._last_control_activity = time.monotonic()
            response = await self.dispatch(request)
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        writer.write((json.dumps(response) + "\n").encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def heartbeat(self) -> None:
        """Send one heartbeat without racing another transport operation."""
        async with self._transport_lock:
            await self._simulator.call("Heartbeat", {})

    async def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        """Handle one local control request against the persistent connection."""
        action = request.get("action")
        if action == "status":
            return {
                "ok": True,
                "charger": self.config.charger,
                "connected": self._simulator.connected,
                "boot": self._boot.status if self._boot else None,
                "reconnects": self._reconnects,
                "idle_seconds": max(
                    0.0, time.monotonic() - self._last_control_activity
                ),
            }
        if action == "authorize":
            id_tag = str(request.get("id_tag", ""))
            async with self._transport_lock:
                status = await self._simulator.authorize(id_tag)
            return {
                "ok": True,
                "charger": self.config.charger,
                "boot": self._boot.status if self._boot else None,
                "authorization": status,
            }
        if action == "authorize-scenario":
            scenario = authorization_policy_scenario(
                policy_context=str(request.get("policy_context", "")),
                known_authorized=str(request.get("known_authorized", "")),
                known_denied=str(request.get("known_denied", "")),
                unknown=str(request.get("unknown", "")),
            )
            async with self._transport_lock:
                results = await run_live_authorization_scenario(
                    self._simulator,
                    scenario,
                )
            return {
                "ok": True,
                "charger": self.config.charger,
                "scenario": scenario.name,
                "policy_context": scenario.policy_context,
                "results": [asdict(result) for result in results],
            }
        if action == "replay":
            source = resolve_replay_database(Path(str(request.get("source", ""))))
            batch_size = int(request.get("batch_size", 250))
            reconnect_after_raw = request.get("reconnect_after")
            reconnect_after = (
                int(reconnect_after_raw) if reconnect_after_raw is not None else None
            )
            pacing = ReplayPacing(
                mode=str(request.get("pacing", "maximum")),
                interval_seconds=float(request.get("interval_seconds", 0.0)),
                burst_size=int(request.get("burst_size", 100)),
                burst_pause_seconds=float(
                    request.get("burst_pause_seconds", 0.0)
                ),
            )
            replay_stream = str(request.get("stream", "transactions"))
            if replay_stream == "transactions":
                event_source = iter_v16_transaction_replay
            elif replay_stream == "inbound":
                event_source = iter_v16_inbound_request_replay
            else:
                raise ValueError("replay stream must be transactions or inbound")
            events = event_source(
                source.database,
                charger_identity=(
                    str(request["source_charger"])
                    if request.get("source_charger")
                    else None
                ),
                batch_size=batch_size,
            )
            metrics = ReplayMetrics()
            async with self._transport_lock:
                completed = await run_v16_live_replay_events(
                    self._simulator,
                    events,
                    reconnect_after=reconnect_after,
                    pacing=pacing,
                    metrics=metrics,
                    max_retained_actions=MAX_REPLAY_ACTIONS_IN_RESPONSE,
                )
            return {
                "ok": True,
                "charger": self.config.charger,
                "source_kind": source.kind,
                "capture_id": source.capture_id,
                "source_charger": request.get("source_charger"),
                "stream": replay_stream,
                "events_completed": metrics.completed_requests,
                "actions": list(completed),
                "actions_truncated": metrics.completed_requests > len(completed),
                "pacing": pacing.mode,
                "reconnect_after": reconnect_after,
                "metrics": metrics.as_dict(),
            }
        if action == "reconnect":
            await self.reconnect()
            return {
                "ok": True,
                "charger": self.config.charger,
                "connected": True,
                "boot": self._boot.status,
                "reconnects": self._reconnects,
            }
        if action == "close":
            self._stop.set()
            return {"ok": True, "charger": self.config.charger, "closed": True}
        raise LiveSimulatorError(f"unknown simulator action: {action!r}")

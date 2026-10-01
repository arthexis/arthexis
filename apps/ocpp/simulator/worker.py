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
from apps.ocpp.simulator.clock import ChargerClock
from apps.ocpp.simulator.database_replay import (
    ReplayMetrics,
    ReplayPacing,
    iter_v16_inbound_request_replay,
    iter_v16_transaction_replay,
    run_v16_live_replay_events,
)
from apps.ocpp.simulator.network import (
    LiveOcpp16Simulator,
    LiveSimulatorConfig,
    LiveSimulatorError,
)
from apps.ocpp.simulator.requests import RequestJournal
from apps.ocpp.simulator.sources import resolve_replay_database

DEFAULT_IDLE_TIMEOUT = 0.0
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


def active_sessions() -> list[dict[str, Any]]:
    sessions = []
    for path in runtime_dir().glob("charger-*.json"):
        try:
            metadata = json.loads(path.read_text())
            charger = str(metadata["charger"])
        except (OSError, KeyError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            continue
        try:
            sessions.append(load_session(charger))
        except LiveSimulatorError:
            continue
    return sessions


def active_session() -> dict[str, Any] | None:
    sessions = active_sessions()
    if not sessions:
        return None
    if len(sessions) > 1:
        identities = ", ".join(sorted(str(item.get("charger")) for item in sessions))
        raise LiveSimulatorError(
            "multiple live simulator sessions found; stop them before continuing: "
            + identities
        )
    return sessions[0]


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
        if self.idle_timeout < 0:
            raise ValueError("idle_timeout must be zero or greater")
        self._last_control_activity = time.monotonic()
        self._stop = asyncio.Event()
        self._simulator = self.simulator_factory(self.config)
        self._boot = None
        self._reconnects = 0
        self._transport_lock = asyncio.Lock()
        self._clock = ChargerClock.from_profile(self.config.clock)
        self._requests = RequestJournal(self.config.evidence_dir)
        self._request_tasks: set[asyncio.Task] = set()
        self._request_events: dict[str, asyncio.Event] = {}

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
                        "lifecycle": "on-demand",
                        "evidence_dir": self.config.evidence_dir,
                        "clock": self.config.clock,
                        "charger_time": self._clock.isoformat(),
                        "csms_time": self._boot.current_time,
                        "authorization_timeout": self.config.authorization_timeout,
                    }
                )
            )
            self._last_control_activity = time.monotonic()
            if self.idle_timeout > 0:
                idle_task = asyncio.create_task(self._idle_watch())
            if self.config.heartbeat:
                heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            async with server:
                await self._stop.wait()
        finally:
            for task in (idle_task, heartbeat_task):
                if task is not None:
                    task.cancel()
            for task in tuple(self._request_tasks):
                task.cancel()
            for task in (idle_task, heartbeat_task, *tuple(self._request_tasks)):
                if task is not None:
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            if server is not None:
                server.close()
                await server.wait_closed()
            await self._simulator.close()
            cleanup_session_artifacts(self.config.charger)

    async def connect_and_boot(self) -> None:
        async with self._transport_lock:
            await self._simulator.connect()
            self._boot = await self._simulator.boot()
        if self._boot.status != "Accepted":
            await self._simulator.close()
            raise LiveSimulatorError(
                f"BootNotification was not accepted: {self._boot.status}"
            )

    async def disconnect(self) -> None:
        async with self._transport_lock:
            await self._simulator.close()

    async def reconnect(self) -> None:
        if not self.config.reconnect_enabled:
            raise LiveSimulatorError("reconnect is disabled by the charger profile")
        async with self._transport_lock:
            await self._simulator.reconnect()
            self._boot = await self._simulator.boot()
        if self._boot.status != "Accepted":
            raise LiveSimulatorError(
                f"BootNotification was not accepted after reconnect: {self._boot.status}"
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
        async with self._transport_lock:
            await self._simulator.call("Heartbeat", {})

    async def _run_authorize(self, request_id: str, id_tag: str) -> None:
        try:
            async with self._transport_lock:
                status = await self._simulator.authorize(id_tag)
            self._requests.completed(
                request_id,
                charger=self.config.charger,
                authorization=status,
                charger_time=self._clock.isoformat(),
            )
        except Exception as exc:
            self._requests.completed(
                request_id,
                charger=self.config.charger,
                error=str(exc),
                charger_time=self._clock.isoformat(),
            )
        finally:
            event = self._request_events.get(request_id)
            if event is not None:
                event.set()

    def _submit_authorize(self, id_tag: str) -> dict[str, Any]:
        if not id_tag:
            raise LiveSimulatorError("authorize requires an idTag")
        request_id = self._requests.new_request_id()
        self._requests.submitted(
            request_id,
            charger=self.config.charger,
            id_tag=id_tag,
            charger_time=self._clock.isoformat(),
        )
        self._request_events[request_id] = asyncio.Event()
        task = asyncio.create_task(self._run_authorize(request_id, id_tag))
        self._request_tasks.add(task)
        task.add_done_callback(self._request_tasks.discard)
        return {
            "ok": True,
            "charger": self.config.charger,
            "request_id": request_id,
            "submitted": True,
            "completed": False,
        }

    async def _request_result(
        self,
        request_id: str,
        *,
        wait: bool,
        timeout: float | None,
    ) -> dict[str, Any]:
        result = self._requests.result(request_id)
        if result is None and wait:
            event = self._request_events.get(request_id)
            if event is None:
                raise LiveSimulatorError(f"unknown simulator request: {request_id}")
            try:
                if timeout is None:
                    await event.wait()
                else:
                    await asyncio.wait_for(event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                return {
                    "ok": True,
                    "charger": self.config.charger,
                    "request_id": request_id,
                    "completed": False,
                    "timed_out": True,
                }
            result = self._requests.result(request_id)
        if result is None:
            if request_id not in self._request_events:
                raise LiveSimulatorError(f"unknown simulator request: {request_id}")
            return {
                "ok": True,
                "charger": self.config.charger,
                "request_id": request_id,
                "completed": False,
            }
        return {"ok": True, "completed": True, **result}

    async def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
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
                "idle_timeout": self.idle_timeout,
                "lifecycle": "on-demand",
                "protocol": self.config.protocol,
                "clock": self._clock.describe(),
                "csms_time": self._boot.current_time if self._boot else None,
                "authorization_timeout": self.config.authorization_timeout,
                "evidence_dir": self.config.evidence_dir,
            }
        if action == "authorize":
            return self._submit_authorize(str(request.get("id_tag", "")))
        if action in {"result", "wait-result"}:
            request_id = str(request.get("request_id", ""))
            if not request_id:
                raise LiveSimulatorError("request result requires request_id")
            timeout_raw = request.get("timeout")
            timeout = float(timeout_raw) if timeout_raw is not None else None
            if timeout is not None and timeout < 0:
                raise ValueError("request timeout must be zero or greater")
            return await self._request_result(
                request_id,
                wait=action == "wait-result",
                timeout=timeout,
            )
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
        if action == "disconnect":
            await self.disconnect()
            return {
                "ok": True,
                "charger": self.config.charger,
                "connected": False,
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

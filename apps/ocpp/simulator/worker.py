"""Persistent local worker that owns one simulated charger's WebSocket."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import OCPP16Simulator, SimulatorConfig, SimulatorError
from .results import AuthorizationResult

DEFAULT_IDLE_TIMEOUT = 300.0
DEFAULT_MAX_INSTANCES = 2
ENV_MAX_INSTANCES = "OCPP_SIMULATOR_MAX_INSTANCES"


def runtime_dir() -> Path:
    root = Path(
        os.environ.get("OCPP_SIMULATOR_RUNTIME_DIR", ".arthexis/ocpp-simulators")
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_name(charger: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in charger)


def session_path(charger: str) -> Path:
    return runtime_dir() / f"{_safe_name(charger)}.json"


def socket_path(charger: str) -> Path:
    return runtime_dir() / f"{_safe_name(charger)}.sock"


def error_path(charger: str) -> Path:
    return runtime_dir() / f"{_safe_name(charger)}.error"


def max_instances() -> int:
    try:
        return max(1, int(os.environ.get(ENV_MAX_INSTANCES, DEFAULT_MAX_INSTANCES)))
    except ValueError:
        return DEFAULT_MAX_INSTANCES


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def cleanup_stale_sessions() -> None:
    for path in runtime_dir().glob("*.json"):
        try:
            metadata = json.loads(path.read_text())
            pid = int(metadata["pid"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            pid = -1
        if pid < 1 or not _pid_alive(pid):
            path.unlink(missing_ok=True)
            path.with_suffix(".sock").unlink(missing_ok=True)


def active_sessions() -> list[dict[str, Any]]:
    cleanup_stale_sessions()
    sessions = []
    for path in runtime_dir().glob("*.json"):
        try:
            sessions.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return sessions


def load_session(charger: str) -> dict[str, Any]:
    cleanup_stale_sessions()
    path = session_path(charger)
    if not path.exists():
        raise SimulatorError(f"simulator {charger!r} is not open")
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SimulatorError(f"simulator {charger!r} has invalid session metadata") from exc


async def send_control(charger: str, request: dict[str, Any]) -> dict[str, Any]:
    metadata = load_session(charger)
    try:
        reader, writer = await asyncio.open_unix_connection(metadata["socket"])
    except (OSError, KeyError) as exc:
        raise SimulatorError(f"cannot contact simulator {charger!r}") from exc
    try:
        writer.write((json.dumps(request) + "\n").encode())
        await writer.drain()
        raw = await reader.readline()
    finally:
        writer.close()
        await writer.wait_closed()
    if not raw:
        raise SimulatorError("simulator worker closed without a response")
    response = json.loads(raw)
    if not response.get("ok"):
        raise SimulatorError(str(response.get("error", "simulator command failed")))
    return response


@dataclass
class SimulatorWorker:
    config: SimulatorConfig
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT

    def __post_init__(self) -> None:
        if self.idle_timeout <= 0:
            raise ValueError("idle_timeout must be greater than zero")
        self._last_control_activity = time.monotonic()
        self._stop = asyncio.Event()
        self._simulator = OCPP16Simulator(self.config)
        self._boot = None

    async def run(self) -> None:
        sock = socket_path(self.config.charger)
        metadata_path = session_path(self.config.charger)
        sock.unlink(missing_ok=True)
        server = None
        idle_task = None
        heartbeat_task = None
        await self._simulator.connect()
        try:
            self._boot = await self._simulator.boot()
            if self._boot.status != "Accepted":
                raise SimulatorError(
                    f"BootNotification was not accepted: {self._boot.status}"
                )
            server = await asyncio.start_unix_server(self._handle_client, path=str(sock))
            metadata = {
                "charger": self.config.charger,
                "url": self.config.url,
                "pid": os.getpid(),
                "socket": str(sock),
                "idle_timeout": self.idle_timeout,
                "boot": self._boot.status,
            }
            metadata_path.write_text(json.dumps(metadata))
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
            sock.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)

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
        interval = getattr(self._boot, "interval", None)
        if not interval or interval <= 0:
            return
        while not self._stop.is_set():
            await asyncio.sleep(interval)
            if self._stop.is_set():
                return
            try:
                await self._simulator.call("Heartbeat", {})
            except SimulatorError:
                self._stop.set()
                return

    async def _handle_client(self, reader, writer) -> None:
        try:
            raw = await reader.readline()
            request = json.loads(raw)
            self._last_control_activity = time.monotonic()
            response = await self._dispatch(request)
        except Exception as exc:  # local control boundary
            response = {"ok": False, "error": str(exc)}
        writer.write((json.dumps(response) + "\n").encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request.get("action")
        if action == "status":
            return {
                "ok": True,
                "charger": self.config.charger,
                "boot": self._boot.status if self._boot else None,
                "idle_seconds": time.monotonic() - self._last_control_activity,
            }
        if action == "authorize":
            id_tag = str(request.get("id_tag", ""))
            if not id_tag.strip():
                raise SimulatorError("id_tag is required")
            status = await self._simulator.authorize(id_tag)
            result = AuthorizationResult(
                charger=self.config.charger,
                boot=self._boot.status if self._boot else "",
                id_tag=id_tag,
                authorization=status,
            )
            return {"ok": True, **result.to_dict()}
        if action == "close":
            self._stop.set()
            return {"ok": True, "charger": self.config.charger, "closed": True}
        raise SimulatorError(f"unknown simulator action: {action!r}")

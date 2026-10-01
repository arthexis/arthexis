"""On-demand system service and local supervisor for the OCPP simulator."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from apps.ocpp.simulator.network import LiveSimulatorConfig, LiveSimulatorError
from apps.ocpp.simulator.worker import (
    LiveSimulatorWorker,
    active_session,
    cleanup_session_artifacts,
    load_session,
    runtime_dir,
    send_control,
    session_path,
)

SERVICE_NAME = "arthexis-simulator"
SERVICE_START_TIMEOUT = 40.0


def service_socket_path() -> Path:
    return runtime_dir() / "service.sock"


async def send_service_control(request: dict[str, Any]) -> dict[str, Any]:
    """Send one command to the config-neutral simulator service supervisor."""
    sock = service_socket_path()
    try:
        reader, writer = await asyncio.open_unix_connection(str(sock))
    except OSError as exc:
        raise LiveSimulatorError("simulator service is not running") from exc
    try:
        writer.write((json.dumps(request) + "\n").encode())
        await writer.drain()
        raw = await reader.readline()
    finally:
        writer.close()
        await writer.wait_closed()
    if not raw:
        raise LiveSimulatorError("simulator service closed without a response")
    response = json.loads(raw)
    if not response.get("ok"):
        raise LiveSimulatorError(str(response.get("error", "simulator service failed")))
    return response


@dataclass
class SimulatorService:
    """Own at most one live worker and expose a stable machine-local control socket."""

    worker_factory: Callable[..., LiveSimulatorWorker] = field(
        default=LiveSimulatorWorker,
        repr=False,
    )

    def __post_init__(self) -> None:
        self._stop = asyncio.Event()
        self._worker_task: asyncio.Task | None = None
        self._charger: str | None = None

    async def run(self) -> None:
        sock = service_socket_path()
        sock.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self._handle_client, path=str(sock))
        sock.chmod(0o600)
        try:
            async with server:
                await self._stop.wait()
        finally:
            server.close()
            await server.wait_closed()
            await self._close_worker()
            sock.unlink(missing_ok=True)

    async def _handle_client(self, reader, writer) -> None:
        try:
            raw = await reader.readline()
            request = json.loads(raw)
            response = await self.dispatch(request)
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        writer.write((json.dumps(response) + "\n").encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request.get("action")
        if action == "boot":
            return await self._boot(request)
        if action == "stop":
            result = await self._stop_worker(request.get("charger"))
            asyncio.get_running_loop().call_soon(self._stop.set)
            return result
        if action == "service-status":
            current = active_session()
            return {
                "ok": True,
                "service": SERVICE_NAME,
                "active": current is not None,
                "charger": current.get("charger") if current else None,
            }
        raise LiveSimulatorError(f"unknown simulator service action: {action!r}")

    async def _boot(self, request: dict[str, Any]) -> dict[str, Any]:
        current = active_session()
        if current is not None:
            raise LiveSimulatorError(
                f"simulator is already active as {current.get('charger')!r}; "
                "stop it before booting another charger"
            )
        if self._worker_task is not None and not self._worker_task.done():
            raise LiveSimulatorError("simulator worker is already starting")

        config_payload = request.get("config")
        if not isinstance(config_payload, dict):
            raise LiveSimulatorError("boot requires simulator config")
        config = LiveSimulatorConfig(**config_payload)
        idle_timeout = float(request.get("idle_timeout", 0.0))
        cleanup_session_artifacts(config.charger)

        worker = self.worker_factory(config=config, idle_timeout=idle_timeout)
        self._charger = config.charger
        self._worker_task = asyncio.create_task(worker.run())
        self._worker_task.add_done_callback(self._worker_done)

        deadline = time.monotonic() + config.timeout
        ready = session_path(config.charger)
        while not ready.exists():
            if self._worker_task.done():
                exc = self._worker_task.exception()
                if exc is not None:
                    raise exc
                raise LiveSimulatorError("simulator worker exited before becoming ready")
            if time.monotonic() >= deadline:
                self._worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._worker_task
                cleanup_session_artifacts(config.charger)
                raise LiveSimulatorError(
                    "simulator worker did not become ready before timeout"
                )
            await asyncio.sleep(0.05)

        metadata = load_session(config.charger)
        return {
            "ok": True,
            "service": SERVICE_NAME,
            "charger": config.charger,
            "endpoint": config.url,
            "open": True,
            "boot": metadata.get("boot"),
            "idle_timeout": idle_timeout,
            "lifecycle": "on-demand",
            "clock": metadata.get("clock"),
            "charger_time": metadata.get("charger_time"),
            "csms_time": metadata.get("csms_time"),
        }

    async def _stop_worker(self, requested: object = None) -> dict[str, Any]:
        current = active_session()
        if current is None:
            return {"ok": True, "service": SERVICE_NAME, "closed": True}
        charger = str(current["charger"])
        if requested and str(requested) != charger:
            raise LiveSimulatorError(
                f"simulator is active as {charger!r}; stop it before targeting "
                f"{requested!r}"
            )
        result = await send_control(charger, {"action": "close"})
        if self._worker_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
        return {**result, "service": SERVICE_NAME}

    async def _close_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            return
        current = active_session()
        if current is not None:
            with contextlib.suppress(Exception):
                await send_control(str(current["charger"]), {"action": "close"})
        with contextlib.suppress(asyncio.CancelledError):
            await self._worker_task

    def _worker_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        # A worker ending for any reason means the on-demand service has no
        # useful work left. Exit cleanly so systemd leaves the disabled unit inactive.
        self._stop.set()


@dataclass
class GwaySimulatorServiceController:
    """Provision and operate the disabled systemd unit through GWAY."""

    manage_path: Path
    python: str = sys.executable
    runner: Callable[..., subprocess.CompletedProcess] = field(
        default=subprocess.run,
        repr=False,
    )
    gway: str | None = None
    timeout: float = SERVICE_START_TIMEOUT

    def __post_init__(self) -> None:
        if self.gway is None:
            self.gway = os.environ.get("GWAY_BIN") or shutil.which("gway") or "/usr/local/bin/gway"

    @property
    def service_command(self) -> list[str]:
        return [
            self.python,
            str(self.manage_path),
            "ocpp_simulator",
            "_service",
        ]

    def _prefix(self) -> list[str]:
        return [] if os.geteuid() == 0 else ["sudo", "-n"]

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        command = [*self._prefix(), str(self.gway), *args]
        return self.runner(
            command,
            cwd=str(self.manage_path.parent),
            check=check,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

    def provision(self) -> None:
        self._run(
            "service",
            "install",
            "--backend",
            "systemd",
            "--system",
            "--name",
            SERVICE_NAME,
            "--no-enable",
            "--",
            *self.service_command,
        )

    def start(self) -> None:
        self._run(
            "service",
            "start",
            "--system",
            "--name",
            SERVICE_NAME,
            "--timeout",
            str(self.timeout),
            "--",
            *self.service_command,
        )

    def stop(self) -> None:
        self._run(
            "service",
            "stop",
            "--system",
            "--name",
            SERVICE_NAME,
            "--timeout",
            str(self.timeout),
            "--",
            *self.service_command,
            check=False,
        )

    def ensure_started(self) -> None:
        # Re-provisioning is intentional: it is idempotent, preserves --no-enable,
        # and refreshes ExecStart when the Arthexis runtime changes after upgrade.
        self.provision()
        self.start()
        deadline = time.monotonic() + self.timeout
        sock = service_socket_path()
        while not sock.exists():
            if time.monotonic() >= deadline:
                self.stop()
                raise LiveSimulatorError(
                    "arthexis-simulator service did not become ready before timeout"
                )
            time.sleep(0.05)

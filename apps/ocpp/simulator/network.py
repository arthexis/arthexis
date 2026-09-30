"""Live, model-independent OCPP 1.6J charge-point transport."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from apps.ocpp.protocol.frames import Call, CallError, CallResult, parse_frame


class LiveSimulatorError(RuntimeError):
    """Raised when a simulated charge point cannot complete a live OCPP call."""


@dataclass(frozen=True)
class LiveSimulatorConfig:
    """Connection and charger-profile settings for a live OCPP 1.6J charge point."""

    url: str
    charger: str
    vendor: str = "Arthexis"
    model: str = "Gway Simulator"
    timeout: float = 30.0
    allow_insecure_ws: bool = False
    protocol: str = "ocpp1.6j"
    serial: str | None = None
    firmware_version: str | None = None
    authorization_timeout: float = 60.0
    heartbeat: bool = True
    reconnect_enabled: bool = True
    clock: dict[str, Any] = field(default_factory=dict)
    evidence_dir: str | None = None

    @property
    def endpoint(self) -> str:
        base = self.url.rstrip("/")
        scheme = urlsplit(base).scheme.lower()
        if scheme not in {"ws", "wss"}:
            raise LiveSimulatorError("simulator URL must use ws:// or wss://")
        if scheme == "ws" and not self.allow_insecure_ws:
            raise LiveSimulatorError(
                "plaintext ws:// is disabled; pass --allow-insecure-ws "
                "for trusted local testing"
            )
        return f"{base}/ocpp/{quote(self.charger, safe='')}"


@dataclass(frozen=True)
class BootResult:
    status: str
    current_time: str
    interval: int


class LiveOcpp16Simulator:
    """Own one live OCPP 1.6J WebSocket and correlate charger calls."""

    subprotocol = "ocpp1.6"

    def __init__(
        self,
        config: LiveSimulatorConfig,
        *,
        connection_factory: Callable[..., Awaitable[Any]] = connect,
    ) -> None:
        self.config = config
        self.connection_factory = connection_factory
        self._connection: Any | None = None
        self._receive_task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future[dict[str, object]]] = {}

    @property
    def connected(self) -> bool:
        return self._connection is not None and self._receive_task is not None

    async def connect(self) -> None:
        if self.connected:
            return
        try:
            connection = await self.connection_factory(
                self.config.endpoint,
                subprotocols=[self.subprotocol],
                open_timeout=self.config.timeout,
            )
        except (WebSocketException, OSError, ValueError) as exc:
            raise LiveSimulatorError(f"failed to connect to CSMS: {exc}") from exc

        negotiated = getattr(connection, "subprotocol", None)
        if negotiated not in {self.subprotocol, "ocpp1.6j"}:
            with contextlib.suppress(WebSocketException, OSError):
                await connection.close()
            raise LiveSimulatorError(
                f"CSMS did not negotiate OCPP 1.6J (received {negotiated!r})"
            )

        self._connection = connection
        self._receive_task = asyncio.create_task(self._receive_loop(connection))

    async def close(self) -> None:
        task, self._receive_task = self._receive_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        connection, self._connection = self._connection, None
        if connection is not None:
            with contextlib.suppress(WebSocketException, OSError):
                await connection.close()

        self._fail_pending(LiveSimulatorError("simulator connection closed"))
        self._pending.clear()

    async def reconnect(self) -> None:
        await self.close()
        await self.connect()

    async def call(self, action: str, payload: dict[str, object]) -> dict[str, object]:
        if not self.connected or self._connection is None:
            raise LiveSimulatorError("simulator is not connected")

        unique_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, object]] = loop.create_future()
        self._pending[unique_id] = future
        deadline = loop.time() + self.config.timeout

        try:
            await self._with_deadline(
                self._connection.send(
                    json.dumps(Call(unique_id, action, payload).to_wire())
                ),
                deadline,
                action,
            )
            return await self._with_deadline(future, deadline, action)
        except (WebSocketException, OSError) as exc:
            raise LiveSimulatorError(
                f"WebSocket failure during {action}: {exc}"
            ) from exc
        finally:
            self._pending.pop(unique_id, None)

    async def boot(self) -> BootResult:
        payload: dict[str, object] = {
            "chargePointVendor": self.config.vendor,
            "chargePointModel": self.config.model,
        }
        if self.config.serial:
            payload["chargePointSerialNumber"] = self.config.serial
        if self.config.firmware_version:
            payload["firmwareVersion"] = self.config.firmware_version
        response = await self.call("BootNotification", payload)
        status = response.get("status")
        current_time = response.get("currentTime")
        interval = response.get("interval")
        if not isinstance(status, str) or not status:
            raise LiveSimulatorError(
                "BootNotification response requires string status"
            )
        if not isinstance(current_time, str) or not current_time:
            raise LiveSimulatorError(
                "BootNotification response requires string currentTime"
            )
        if isinstance(interval, bool) or not isinstance(interval, int) or interval < 0:
            raise LiveSimulatorError(
                "BootNotification response requires a non-negative integer interval"
            )
        return BootResult(status=status, current_time=current_time, interval=interval)

    async def authorize(self, id_tag: str) -> str:
        if not id_tag.strip():
            raise LiveSimulatorError("id_tag is required")
        response = await self.call("Authorize", {"idTag": id_tag})
        info = response.get("idTagInfo")
        if not isinstance(info, dict):
            raise LiveSimulatorError(
                "Authorize response did not contain idTagInfo.status"
            )
        status = info.get("status")
        if not isinstance(status, str) or not status:
            raise LiveSimulatorError(
                "Authorize response did not contain idTagInfo.status"
            )
        return status

    async def _receive_loop(self, connection: Any) -> None:
        task = asyncio.current_task()
        try:
            while self._connection is connection:
                raw = await connection.recv()
                try:
                    frame = parse_frame(json.loads(raw))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue

                if isinstance(frame, CallResult):
                    future = self._pending.get(frame.unique_id)
                    if future is not None and not future.done():
                        future.set_result(frame.payload)
                elif isinstance(frame, CallError):
                    future = self._pending.get(frame.unique_id)
                    if future is not None and not future.done():
                        future.set_exception(
                            LiveSimulatorError(
                                f"OCPP call failed: {frame.code}: {frame.description}"
                            )
                        )
                elif isinstance(frame, Call):
                    await connection.send(
                        json.dumps(
                            CallError(
                                unique_id=frame.unique_id,
                                code="NotSupported",
                                description=(
                                    f"Simulator does not implement {frame.action}"
                                ),
                                details={},
                            ).to_wire()
                        )
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail_pending(
                LiveSimulatorError(f"connection receive failed: {exc}")
            )
        finally:
            if self._connection is connection:
                self._connection = None
            if self._receive_task is task:
                self._receive_task = None
            with contextlib.suppress(WebSocketException, OSError):
                await connection.close()

    async def _with_deadline(self, awaitable, deadline: float, action: str):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise LiveSimulatorError(f"timed out waiting for {action} response")
        try:
            return await asyncio.wait_for(awaitable, timeout=remaining)
        except TimeoutError as exc:
            raise LiveSimulatorError(
                f"timed out waiting for {action} response"
            ) from exc

    def _fail_pending(self, error: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)

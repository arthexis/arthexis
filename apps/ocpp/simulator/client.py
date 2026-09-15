"""Small, model-independent OCPP 1.6J charge-point client."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from websockets.asyncio.client import connect

from apps.ocpp.consumers.constants import OCPP_VERSION_16
from apps.ocpp.consumers.csms.protocol import (
    OCPPCallEnvelope,
    OCPPCallErrorEnvelope,
    OCPPCallResultEnvelope,
    validate_message_envelope,
)

from .results import BootResult


class SimulatorError(RuntimeError):
    """Raised when the simulated charge point cannot complete an OCPP call."""


@dataclass(frozen=True)
class SimulatorConfig:
    """Plain configuration for a simulated OCPP 1.6 charge point."""

    url: str
    charger: str
    vendor: str = "ArtHexis"
    model: str = "Gway Simulator"
    timeout: float = 30.0

    @property
    def endpoint(self) -> str:
        base = self.url.rstrip("/")
        return f"{base}/ocpp/{quote(self.charger, safe='')}"


class OCPP16Simulator:
    """Reusable OCPP 1.6J connection capable of multiple sequential calls."""

    def __init__(self, config: SimulatorConfig) -> None:
        self.config = config
        self._connection: Any | None = None
        self._receive_task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}

    async def connect(self) -> None:
        if self._connection is not None:
            return
        self._connection = await connect(
            self.config.endpoint,
            subprotocols=[str(OCPP_VERSION_16)],
            open_timeout=self.config.timeout,
        )
        negotiated = getattr(self._connection, "subprotocol", None)
        if negotiated not in {str(OCPP_VERSION_16), "ocpp1.6j"}:
            await self.close()
            raise SimulatorError(
                f"CSMS did not negotiate OCPP 1.6J (received {negotiated!r})"
            )
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def close(self) -> None:
        task, self._receive_task = self._receive_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        connection, self._connection = self._connection, None
        if connection is not None:
            await connection.close()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(SimulatorError("simulator connection closed"))
        self._pending.clear()

    async def call(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._connection is None:
            raise SimulatorError("simulator is not connected")
        message_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        await self._connection.send(json.dumps([2, message_id, action, payload]))
        try:
            return await asyncio.wait_for(future, timeout=self.config.timeout)
        except TimeoutError as exc:
            raise SimulatorError(f"timed out waiting for {action} response") from exc
        finally:
            self._pending.pop(message_id, None)

    async def _receive_loop(self) -> None:
        try:
            while self._connection is not None:
                raw = await self._connection.recv()
                try:
                    message = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                envelope = validate_message_envelope(message)
                if isinstance(envelope, (OCPPCallResultEnvelope, OCPPCallErrorEnvelope)):
                    future = self._pending.get(envelope.message_id)
                    if future is None or future.done():
                        continue
                    if isinstance(envelope, OCPPCallResultEnvelope):
                        future.set_result(dict(envelope.payload))
                    else:
                        future.set_exception(
                            SimulatorError(
                                f"OCPP call failed: {envelope.error_code}: {envelope.description}"
                            )
                        )
                    continue
                if isinstance(envelope, OCPPCallEnvelope):
                    await self._handle_csms_call(envelope)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(SimulatorError(f"connection receive failed: {exc}"))

    async def _handle_csms_call(self, envelope: OCPPCallEnvelope) -> None:
        """Reject unsupported CSMS actions explicitly until they are simulated."""
        if self._connection is not None:
            await self._connection.send(
                json.dumps(
                    [
                        4,
                        envelope.message_id,
                        "NotSupported",
                        f"Simulator does not implement {envelope.action}",
                        {},
                    ]
                )
            )

    async def boot(self) -> BootResult:
        response = await self.call(
            "BootNotification",
            {
                "chargePointVendor": self.config.vendor,
                "chargePointModel": self.config.model,
            },
        )
        interval = response.get("interval")
        return BootResult(
            status=str(response.get("status", "")),
            current_time=(
                str(response["currentTime"]) if response.get("currentTime") else None
            ),
            interval=int(interval) if interval is not None else None,
        )

    async def authorize(self, id_tag: str) -> str:
        response = await self.call("Authorize", {"idTag": id_tag})
        info = response.get("idTagInfo")
        if not isinstance(info, dict) or not info.get("status"):
            raise SimulatorError("Authorize response did not contain idTagInfo.status")
        return str(info["status"])

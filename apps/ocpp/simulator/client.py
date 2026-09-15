"""Small, model-independent OCPP 1.6J charge-point client."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from websockets.asyncio.client import connect

from apps.ocpp.consumers.constants import OCPP_VERSION_16
from apps.ocpp.consumers.csms.protocol import (
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

    async def __aenter__(self) -> OCPP16Simulator:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

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

    async def close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            await connection.close()

    async def call(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._connection is None:
            raise SimulatorError("simulator is not connected")
        message_id = uuid.uuid4().hex
        await self._connection.send(json.dumps([2, message_id, action, payload]))

        while True:
            try:
                raw = await asyncio.wait_for(
                    self._connection.recv(), timeout=self.config.timeout
                )
            except asyncio.TimeoutError as exc:
                raise SimulatorError(f"timed out waiting for {action} response") from exc
            try:
                message = json.loads(raw)
            except (TypeError, json.JSONDecodeError) as exc:
                raise SimulatorError("CSMS returned invalid JSON") from exc
            envelope = validate_message_envelope(message)
            if isinstance(envelope, OCPPCallResultEnvelope):
                if envelope.message_id == message_id:
                    return dict(envelope.payload)
                continue
            if isinstance(envelope, OCPPCallErrorEnvelope):
                if envelope.message_id == message_id:
                    raise SimulatorError(
                        f"{action} failed: {envelope.error_code}: {envelope.description}"
                    )
                continue

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

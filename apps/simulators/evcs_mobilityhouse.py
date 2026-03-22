"""Mobility House based EVCS simulator runtime and proposal helpers."""

from __future__ import annotations

import asyncio
import base64
import json
import random
import threading
import time
import urllib.parse
from dataclasses import dataclass
from importlib.util import find_spec
from typing import Any, Mapping, Optional
import websockets



from apps.ocpp import store
from apps.ocpp.utils import resolve_ws_scheme
from apps.simulators.network import validate_simulator_endpoint
from apps.simulators.simulator_runtime import normalize_simulator_params


class MobilityHouseOcppUnavailableError(ModuleNotFoundError):
    """Raised when the optional `ocpp` package is not installed."""


@dataclass(slots=True)
class MobilityHouseSimulatorConfig:
    """Runtime configuration contract for the Mobility House adapter."""

    charge_point_id: str
    central_system_uri: str
    heartbeat_interval_s: int = 30
    meter_interval_s: float = 10.0
    interval_s: float = 5.0
    start_delay_s: float = 0.0
    reconnect_slots: str | None = None
    demo_mode: bool = False
    vendor: str = "ArthexisSimulator"
    model: str = "EVCS-v2"
    rfid: str = "FFFFFFFF"
    vin: str = ""
    serial_number: str = ""
    connector_id: int = 1
    duration: int = 600
    average_kwh: float = 60.0
    amperage: float = 90.0
    repeat: bool | object = False
    username: str | None = None
    password: str | None = None
    allow_private_network: bool = False
    ws_scheme: str | None = None
    use_tls: bool | None = None

    @classmethod
    def from_payload(
        cls, values: Mapping[str, object], *, cp_idx: int | None = None
    ) -> "MobilityHouseSimulatorConfig":
        normalized = normalize_simulator_params(values, cp_idx=cp_idx or 1)
        path = normalized.cp_path.strip("/")
        if not path:
            path = f"CP{normalized.cp_idx}"

        scheme = normalized.ws_scheme or resolve_ws_scheme()
        if normalized.use_tls is True:
            scheme = "wss"
        elif normalized.use_tls is False:
            scheme = "ws"

        if normalized.ws_port:
            uri = f"{scheme}://{normalized.host}:{normalized.ws_port}/{path}"
        else:
            uri = f"{scheme}://{normalized.host}/{path}"

        return cls(
            charge_point_id=path,
            central_system_uri=uri,
            heartbeat_interval_s=30,
            meter_interval_s=normalized.meter_interval,
            interval_s=normalized.interval,
            start_delay_s=normalized.start_delay,
            reconnect_slots=normalized.reconnect_slots,
            demo_mode=normalized.demo_mode,
            rfid=normalized.rfid,
            vin=normalized.vin,
            serial_number=normalized.serial_number or path,
            connector_id=normalized.connector_id,
            duration=normalized.duration,
            average_kwh=normalized.average_kwh,
            amperage=normalized.amperage,
            repeat=normalized.repeat,
            username=normalized.username,
            password=normalized.password,
            allow_private_network=normalized.allow_private_network,
            ws_scheme=normalized.ws_scheme,
            use_tls=normalized.use_tls,
        )


@dataclass
class MobilityHouseSimulatorProposal:
    """A proposal object describing the EVCS v2 runtime architecture."""

    config: MobilityHouseSimulatorConfig
    adapter_path: str
    notes: tuple[str, ...]


def ensure_mobilityhouse_ocpp_available() -> None:
    """Validate that the optional `ocpp` package is importable."""

    if find_spec("ocpp") is None:
        raise MobilityHouseOcppUnavailableError(
            "Install the 'ocpp' package to enable the Mobility House EVCS simulator."
        )


def build_simulator_proposal(
    config: MobilityHouseSimulatorConfig,
) -> MobilityHouseSimulatorProposal:
    """Build a structured EVCS simulator v2 proposal."""

    ensure_mobilityhouse_ocpp_available()
    return MobilityHouseSimulatorProposal(
        config=config,
        adapter_path=(
            f"{MobilityHouseChargePointAdapter.__module__}"
            f".{MobilityHouseChargePointAdapter.__qualname__}"
        ),
        notes=(
            "Mobility House adapter executes OCPP Boot/Authorize/StartTransaction/MeterValues/StopTransaction lifecycle.",
            "Demo mode, reconnect slots, and start delay are tracked in legacy-compatible state payloads.",
            "Enable ocpp-simulator-v2 suite feature for runtime rollout.",
        ),
    )


def _parse_repeat(value: object) -> float:
    if value is True:
        return float("inf")
    if isinstance(value, str) and value.strip().lower() in {"true", "on", "forever", "infinite", "loop"}:
        return float("inf")
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1
    return parsed if parsed > 0 else 1


class MobilityHouseChargePointAdapter:
    """Runtime adapter for OCPP v2 simulation."""

    def __init__(
        self,
        config: MobilityHouseSimulatorConfig,
        *,
        sim_state: Any | None = None,
    ) -> None:
        self.config = config
        self.sim_state = sim_state
        self.status = "stopped"
        self._thread: threading.Thread | None = None
        self._connected = threading.Event()
        self._stop_event = threading.Event()
        self._connect_error = ""
        self._runner = None

    @property
    def log_name(self) -> str:
        return str(store._file_path(self.config.charge_point_id, log_type="simulator"))

    def _log(self, message: str) -> None:
        store.add_log(self.config.charge_point_id, message, log_type="simulator")

    def _auth_headers(self) -> dict[str, str]:
        if self.config.username and self.config.password:
            token = f"{self.config.username}:{self.config.password}"
            b64 = base64.b64encode(token.encode("utf-8")).decode("ascii")
            return {"Authorization": f"Basic {b64}"}
        return {}

    def _normalize_message_id(self, value: object) -> str:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
        return str(value)

    async def _connect(self):
        scheme = self.config.ws_scheme or resolve_ws_scheme()
        if self.config.use_tls is True:
            scheme = "wss"
        elif self.config.use_tls is False:
            scheme = "ws"

        candidate_schemes: list[str] = [scheme]
        fallback_scheme = "wss" if scheme == "ws" else "ws"
        if fallback_scheme != scheme:
            candidate_schemes.append(fallback_scheme)

        last_error: Exception | None = None
        headers = self._auth_headers()
        connect_kwargs: dict[str, object] = {}
        if headers:
            connect_kwargs["additional_headers"] = headers

        ws = None
        for candidate in candidate_schemes:
            try:
                ws = await asyncio.wait_for(
                    __import__("websockets").connect(
                        self.config.central_system_uri.replace(
                            urllib.parse.urlparse(self.config.central_system_uri).scheme,
                            candidate,
                            1,
                        ),
                        subprotocols=["ocpp1.6"],
                        **connect_kwargs,
                    ),
                    timeout=10,
                )
                self._log(f"Connected (subprotocol={candidate})")
                return ws
            except Exception as exc:  # pragma: no cover - network dependent
                last_error = exc
                self._log(f"Connection failed ({candidate}): {exc}")

            try:
                ws = await asyncio.wait_for(
                    __import__("websockets").connect(
                        self.config.central_system_uri.replace(
                            urllib.parse.urlparse(self.config.central_system_uri).scheme,
                            candidate,
                        ),
                        **connect_kwargs,
                    ),
                    timeout=10,
                )
                return ws
            except Exception as exc:  # pragma: no cover - network dependent
                last_error = exc
                self._log(f"Connection failed without subprotocol ({candidate}): {exc}")

        if last_error is None:
            raise RuntimeError("Unable to establish simulator websocket connection")
        raise last_error

    async def _send(self, ws, payload: list[object], *, message_id: str | None = None) -> str:
        payload_json = json.dumps(payload)
        await ws.send(payload_json)
        if message_id is None and len(payload) >= 2:
            message_id = str(payload[1])
        self._log(f"> {payload_json}")
        return str(message_id)

    async def _recv(self, ws, *, timeout: float = 60.0):
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        except Exception as exc:  # pragma: no cover - network dependent
            raise RuntimeError(f"Failed to receive OCPP message: {exc}") from exc
        self._log(f"< {raw}")
        try:
            return json.loads(raw)
        except Exception as exc:  # pragma: no cover - protocol dependent
            raise RuntimeError(f"Invalid JSON payload from CSMS: {exc}") from exc

    async def _consume_calls(self, ws, message_id: str) -> tuple[object, list[object] | None]:
        command_id = 0
        while True:
            incoming = await self._recv(ws)
            if not isinstance(incoming, list) or len(incoming) < 3:
                continue
            frame_type = incoming[0]
            if frame_type == 4:
                # Ignore protocol errors, keep the loop moving.
                continue
            if frame_type != 2:
                if message_id and frame_type == 3 and incoming[1] == message_id:
                    return incoming[2], []
                if message_id and frame_type == 4 and len(incoming) >= 4 and incoming[1] == message_id:
                    return incoming[3], []
                continue

            call_id = self._normalize_message_id(incoming[1])
            action = str(incoming[2]) if len(incoming) > 2 else ""
            payload = incoming[3] if len(incoming) > 3 and isinstance(incoming[3], dict) else {}

            if action == "RemoteStopTransaction":
                await self._send(ws, [3, call_id, {}], message_id=call_id)
                self._stop_event.set()
                if self.sim_state is not None:
                    self.sim_state.last_status = "Remote stop requested"
                    self.sim_state.phase = "Stopping"
                command_id += 1
                continue

            if action == "Reset":
                await self._send(ws, [3, call_id, {}], message_id=call_id)
                self._stop_event.set()
                if self.sim_state is not None:
                    self.sim_state.last_status = "Reset requested"
                    self.sim_state.phase = "Stopping"
                command_id += 1
                continue

            await self._send(
                ws,
                [4, call_id, "NotSupported", f"Simulator does not implement {action}", {}],
                message_id=call_id,
            )
            if self.sim_state is not None:
                self.sim_state.last_error = f"Unsupported action {action}"

    async def _read_response(
        self,
        ws,
        *,
        expected_message_id: str | None = None,
        timeout: float = 60.0,
    ) -> tuple[object, list[object] | None]:
        deadline = time.monotonic() + timeout
        call: list[object] | None = None
        while True:
            remaining = max(0.1, timeout if not expected_message_id else deadline - time.monotonic())
            incoming = await self._recv(ws, timeout=remaining)
            if not isinstance(incoming, list) or not incoming:
                continue
            frame_type = incoming[0]
            if frame_type == 2:
                call = incoming
                await self._consume_calls(ws, expected_message_id or "")
                continue
            if frame_type == 3 and len(incoming) >= 3:
                response_id = self._normalize_message_id(incoming[1])
                if expected_message_id is None or response_id == expected_message_id:
                    return incoming[2], call
            if frame_type == 4 and expected_message_id is None:
                if len(incoming) >= 2:
                    return incoming[2:] if len(incoming) > 2 else {}, call
            if expected_message_id and frame_type == 4 and len(incoming) >= 2:
                response_id = self._normalize_message_id(incoming[1])
                if response_id == expected_message_id:
                    return incoming[2:] if len(incoming) > 2 else {}, call

    async def _run_once(self, ws, *, message_counter: int) -> None:
        meter_interval = self.config.meter_interval_s
        if meter_interval <= 0:
            meter_interval = self.config.interval_s

        await self._send(
            ws,
            [
                2,
                f"boot-{message_counter}",
                "BootNotification",
                {
                    "chargePointVendor": self.config.vendor,
                    "chargePointModel": self.config.model,
                    "chargePointSerialNumber": self.config.serial_number,
                },
            ],
        )
        boot_response, _ = await self._read_response(ws, expected_message_id=f"boot-{message_counter}")
        if not isinstance(boot_response, dict) or str(
            boot_response.get("status", "")
        ).lower() != "accepted":
            raise RuntimeError("Boot failed")

        await self._send(
            ws,
            [
                2,
                f"auth-{message_counter}",
                "Authorize",
                {"idTag": self.config.rfid},
            ],
        )
        await self._read_response(ws, expected_message_id=f"auth-{message_counter}")

        if self.config.duration <= 0:
            return

        if self.config.pre_charge_delay > 0:
            start_delay = time.monotonic()
            while not self._stop_event.is_set() and (
                time.monotonic() - start_delay
            ) < float(self.config.pre_charge_delay):
                await self._send(
                    ws,
                    [2, f"hb-pre-{message_counter}", "Heartbeat", {}],
                )
                await self._read_response(ws, timeout=self.config.interval_s, expected_message_id=f"hb-pre-{message_counter}")
                await asyncio.sleep(self.config.interval_s)

        await self._send(
            ws,
            [
                2,
                f"start-{message_counter}",
                "StartTransaction",
                {
                    "connectorId": self.config.connector_id,
                    "idTag": self.config.rfid,
                    "meterStart": random.randint(1000, 2000),
                    "vin": self.config.vin,
                },
            ],
        )
        start_response, _ = await self._read_response(ws, expected_message_id=f"start-{message_counter}")
        tx_id = (start_response or {}).get("transactionId") if isinstance(start_response, dict) else None

        start_time = time.monotonic()
        meter = random.randint(1000, 2000)
        steps = max(1, int(self.config.duration / max(1.0, meter_interval)))
        step_avg = (
            max(0.0, float(self.config.average_kwh)) * 1000.0 / steps
            if steps
            else 0.0
        )

        while (
            time.monotonic() - start_time < self.config.duration
            and not self._stop_event.is_set()
        ):
            meter += max(1, int(step_avg * random.uniform(0.9, 1.1)))
            await self._send(
                ws,
                [
                    2,
                    f"meter-{message_counter}",
                    "MeterValues",
                    {
                        "connectorId": self.config.connector_id,
                        "transactionId": tx_id,
                        "meterValue": [
                            {
                                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "sampledValue": [
                                    {
                                        "value": f"{meter / 1000.0:.3f}",
                                        "measurand": "Energy.Active.Import.Register",
                                        "unit": "kWh",
                                    },
                                    {
                                        "value": f"{self.config.amperage:.3f}",
                                        "measurand": "Current.Import",
                                        "unit": "A",
                                    },
                                ],
                            }
                        ],
                    },
                ],
            )
            await self._read_response(ws, expected_message_id=f"meter-{message_counter}")
            await asyncio.sleep(meter_interval)

        await self._send(
            ws,
            [
                2,
                f"stop-{message_counter}",
                "StopTransaction",
                {
                    "transactionId": tx_id,
                    "idTag": self.config.rfid,
                    "meterStop": meter,
                },
            ],
        )
        await self._read_response(ws, expected_message_id=f"stop-{message_counter}")
        await self._send(
            ws,
            [
                2,
                f"hb-{message_counter}",
                "Heartbeat",
                {},
            ],
        )
        await self._read_response(ws, expected_message_id=f"hb-{message_counter}")

    async def run(self) -> None:
        ensure_mobilityhouse_ocpp_available()

        parsed = urllib.parse.urlparse(self.config.central_system_uri)
        host = parsed.hostname or self.config.central_system_uri
        validate_simulator_endpoint(
            host,
            parsed.port,
            allow_private_network=self.config.allow_private_network,
        )

        if self.config.start_delay_s > 0:
            await asyncio.sleep(self.config.start_delay_s)

        if self.sim_state is not None:
            self.sim_state.phase = "Starting"

        ws = None
        repeat_count = _parse_repeat(self.config.repeat)
        loops = 0
        message_counter = 0
        try:
            while repeat_count == float("inf") or loops < repeat_count:
                if self._stop_event.is_set():
                    break

                message_counter += 1
                if self.sim_state is not None:
                    self.sim_state.phase = "Connecting"
                ws = await self._connect()
                if self.sim_state is not None:
                    self.sim_state.running = True
                    self.sim_state.phase = "Connected"
                    self.sim_state.last_status = "Connected"
                self.status = "running"
                self._connected.set()
                self._connect_error = "accepted"

                try:
                    if self.sim_state is not None:
                        self.sim_state.phase = "Running"
                    await self._run_once(ws, message_counter=message_counter)
                finally:
                    await ws.close()
                    if self.sim_state is not None:
                        self.sim_state.phase = "Stopped"
                        self.sim_state.last_status = "Stopped"

                loops += 1
                if self._stop_event.is_set() or repeat_count != float("inf"):
                    break
                if self.config.duration <= 0:
                    break
                await asyncio.sleep(1)

            self.status = "stopped"
            if self.sim_state is not None:
                self.sim_state.running = False
                self.sim_state.phase = "Stopped"
                self.sim_state.stop_time = time.strftime("%Y-%m-%d %H:%M:%S")
        except asyncio.CancelledError:
            self.status = "stopped"
            if self.sim_state is not None:
                self.sim_state.running = False
            raise
        except Exception as exc:  # pragma: no cover - network/protocol dependent
            self.status = "error"
            self._connect_error = str(exc)
            if self.sim_state is not None:
                self.sim_state.last_error = str(exc)
                self.sim_state.last_status = "Connection failed"
                self.sim_state.running = False
                self.sim_state.phase = "Error"
        finally:
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    pass

    def start(self) -> tuple[bool, str, str]:
        if self._thread and self._thread.is_alive():
            return (
                False,
                "already running",
                self.log_name,
            )

        self._connected.clear()
        self._stop_event.clear()
        self._connect_error = ""

        def runner() -> None:
            asyncio.run(self.run())

        self._thread = threading.Thread(target=runner, daemon=True)
        self._thread.start()

        if not self._connected.wait(15):
            self.status = "error"
            self._connect_error = "timeout"
            if self.sim_state is not None:
                self.sim_state.running = False
                self.sim_state.last_status = "Connection timeout"
                self.sim_state.last_error = self._connect_error
            return False, "Connection timeout", self.log_name

        if self._connect_error == "accepted":
            self.status = "running"
            if self.sim_state is not None:
                self.sim_state.running = True
                self.sim_state.last_status = "Connection accepted"
            return True, "Connection accepted", self.log_name

        if self._connect_error:
            self.status = "error"
            if self.sim_state is not None:
                self.sim_state.running = False
                self.sim_state.last_status = f"Connection failed: {self._connect_error}"
            return False, f"Connection failed: {self._connect_error}", self.log_name

        self.status = "error"
        return False, "Connection failed", self.log_name

    async def stop(self) -> None:
        self._stop_event.set()
        if self.sim_state is not None:
            self.sim_state.phase = "Stopping"
            self.sim_state.last_status = "Stopping"
        if self._thread and self._thread.is_alive():
            await asyncio.to_thread(self._thread.join, timeout=2)
        self._thread = None
        self.status = "stopped"

    def stop_now(self) -> None:
        self._stop_event.set()
        if self.sim_state is not None:
            self.sim_state.phase = "Stopping"
            self.sim_state.last_status = "Stopping"

    def trigger_door_open(self) -> None:
        self._log("DoorOpen command not supported for Mobility House adapter")


__all__ = [
    "MobilityHouseOcppUnavailableError",
    "MobilityHouseSimulatorConfig",
    "MobilityHouseSimulatorProposal",
    "MobilityHouseChargePointAdapter",
    "build_simulator_proposal",
    "ensure_mobilityhouse_ocpp_available",
]






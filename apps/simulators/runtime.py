"""Runtime implementation for the legacy EVCS websocket simulator."""

from __future__ import annotations

import asyncio
import base64
import json
import random
import time
from dataclasses import dataclass
from typing import Any, Callable

import websockets

from apps.ocpp.utils import resolve_ws_scheme


def _ocpp_subprotocol_16j() -> str:
    from apps.ocpp.consumers.constants import OCPP_SUBPROTOCOL_16J

    return OCPP_SUBPROTOCOL_16J


@dataclass(slots=True)
class ChargePointRuntimeConfig:
    cp_idx: int
    host: str
    ws_port: int | None
    rfid: str
    vin: str
    cp_path: str
    serial_number: str
    connector_id: int
    duration: int
    average_kwh: float
    amperage: float
    pre_charge_delay: float
    session_count: float
    interval: float
    start_delay: float
    meter_interval: float | None
    username: str | None
    password: str | None
    ws_scheme: str | None
    use_tls: bool | None


class ChargePointRuntime:
    """Run a single legacy EVCS charge point simulator session."""

    CALL_MESSAGE = 2
    CALL_RESULT_MESSAGE = 3
    CALL_ERROR_MESSAGE = 4

    def __init__(
        self,
        config: ChargePointRuntimeConfig,
        *,
        sim_state: Any,
        log: Callable[[str], None],
        save_state: Callable[[], None],
        connect: Callable[..., Any] = websockets.connect,
    ) -> None:
        self.config = config
        self.state = sim_state
        self.log = log
        self.save_state = save_state
        self._connect = connect
        self.start_delay = max(0.0, float(config.start_delay))
        self.meter_interval = (
            config.interval
            if (config.meter_interval is None or config.meter_interval <= 0)
            else float(config.meter_interval)
        )
        self._ws_path = config.cp_path.lstrip("/")
        self.connect_kwargs: dict[str, object] = {}

        scheme = resolve_ws_scheme(ws_scheme=config.ws_scheme, use_tls=config.use_tls)
        fallback_scheme = "wss" if scheme == "ws" else "ws"
        self._candidate_schemes = [scheme]
        if fallback_scheme != scheme:
            self._candidate_schemes.append(fallback_scheme)

        if config.username and config.password:
            userpass = f"{config.username}:{config.password}"
            b64 = base64.b64encode(userpass.encode("utf-8")).decode("ascii")
            self.connect_kwargs["additional_headers"] = {"Authorization": f"Basic {b64}"}

    def _build_uri(self, scheme: str) -> str:
        base_uri = (
            f"{scheme}://{self.config.host}:{self.config.ws_port}"
            if self.config.ws_port
            else f"{scheme}://{self.config.host}"
        )
        return f"{base_uri}/{self._ws_path}"

    async def send(self, ws: Any, payload: list[object]) -> None:
        text = json.dumps(payload)
        await ws.send(text)
        self.log(f"> {text}")

    async def recv(self, ws: Any) -> str:
        raw = await ws.recv()
        self.log(f"< {raw}")
        return raw

    def jitter(self, value: float) -> float:
        return value * random.uniform(0.95, 1.05)

    async def establish_connection(self) -> Any:
        ws = None
        last_error: Exception | None = None
        for idx, scheme in enumerate(self._candidate_schemes):
            uri = self._build_uri(scheme)
            for attempt in range(2):
                try:
                    ws = await self._connect(
                        uri, subprotocols=[_ocpp_subprotocol_16j()], **self.connect_kwargs
                    )
                    break
                except Exception as exc:
                    last_error = exc
                    self.log(
                        "Connection with subprotocol failed "
                        f"({scheme}, attempt {attempt + 1}): {exc}"
                    )
                    if attempt < 1:
                        self.log("Retrying connection with subprotocol")
            if ws is None:
                try:
                    ws = await self._connect(uri, **self.connect_kwargs)
                except Exception as exc:
                    last_error = exc
                    self.log(f"Connection failed ({scheme}): {exc}")
                    if idx < len(self._candidate_schemes) - 1:
                        next_scheme = self._candidate_schemes[idx + 1]
                        self.log(f"Retrying connection with scheme {next_scheme}")
                    continue
            if ws:
                break

        if ws is None:
            raise last_error if last_error else RuntimeError(
                "Unable to establish simulator websocket connection"
            )

        self.state.phase = "Connected"
        self.state.last_message = ""
        self.log(f"Connected (subprotocol={ws.subprotocol or 'none'})")
        return ws

    async def perform_ocpp_handshake(self, ws: Any) -> None:
        await self.send(
            ws,
            [
                2,
                "boot",
                "BootNotification",
                {
                    "chargePointModel": "Simulator",
                    "chargePointVendor": "SimVendor",
                    "chargePointSerialNumber": self.config.serial_number,
                },
            ],
        )
        self.state.last_message = "BootNotification"
        await self.recv(ws)

        await self.send(ws, [2, "auth", "Authorize", {"idTag": self.config.rfid}])
        self.state.last_message = "Authorize"
        await self.recv(ws)
        self.state.phase = "Available"

    def setup_command_listener(self, ws: Any) -> tuple[asyncio.Event, asyncio.Event, asyncio.Task[Any]]:
        stop_event = asyncio.Event()
        reset_event = asyncio.Event()

        async def listen() -> None:
            def terminate(message: str) -> None:
                self.state.last_error = message
                self.state.last_status = message
                self.state.phase = "Error"
                self.state.running = False
                stop_event.set()
                self.log(message)

            try:
                while True:
                    raw = await self.recv(ws)
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        terminate("Received non-JSON response from CSMS; terminating simulator")
                        return

                    if not isinstance(msg, list) or not msg:
                        terminate("Received invalid OCPP frame from CSMS; terminating simulator")
                        return

                    message_type = msg[0]
                    if message_type in (self.CALL_RESULT_MESSAGE, self.CALL_ERROR_MESSAGE):
                        self.log(
                            f"Received response message type {message_type} from CSMS; ignoring in command listener"
                        )
                        continue
                    if message_type != self.CALL_MESSAGE:
                        terminate(
                            f"Received unsupported message type {message_type} from CSMS; terminating simulator"
                        )
                        return

                    raw_msg_id = msg[1] if len(msg) > 1 else ""
                    msg_id = str(raw_msg_id)
                    action = msg[2] if len(msg) > 2 else ""
                    action_name = str(action)

                    if action_name == "RemoteStopTransaction":
                        await self.send(ws, [self.CALL_RESULT_MESSAGE, msg_id, {}])
                        self.state.last_message = "RemoteStopTransaction"
                        stop_event.set()
                        continue

                    if action_name == "Reset":
                        await self.send(ws, [self.CALL_RESULT_MESSAGE, msg_id, {}])
                        self.state.last_message = "Reset"
                        reset_event.set()
                        stop_event.set()
                        continue

                    await self.send(
                        ws,
                        [
                            self.CALL_ERROR_MESSAGE,
                            msg_id,
                            "NotSupported",
                            f"Simulator does not implement {action_name}",
                            {},
                        ],
                    )
                    self.state.last_message = action_name
                    terminate(
                        f"Received unsupported action '{action_name}' from CSMS; terminating simulator"
                    )
                    return
            except websockets.ConnectionClosed:
                stop_event.set()

        return stop_event, reset_event, asyncio.create_task(listen())

    def metering_plan(self) -> tuple[int, float]:
        meter_start = random.randint(1000, 2000)
        steps = max(1, int(self.config.duration / self.meter_interval))
        target_kwh = self.jitter(self.config.average_kwh)
        step_avg = (target_kwh * 1000) / steps if steps else target_kwh * 1000
        return meter_start, step_avg

    async def pre_charge(self, ws: Any, meter_start: int, step_avg: float) -> int:
        if self.config.pre_charge_delay <= 0:
            return meter_start

        start_delay = time.monotonic()
        next_meter = meter_start
        last_mv = time.monotonic()
        while time.monotonic() - start_delay < self.config.pre_charge_delay:
            await self.send(ws, [2, "hb", "Heartbeat", {}])
            self.state.last_message = "Heartbeat"
            await self.recv(ws)
            await asyncio.sleep(self.meter_interval)
            if time.monotonic() - last_mv >= 30:
                idle_step = max(2, int(step_avg / 100))
                next_meter += random.randint(0, idle_step)
                next_kwh = next_meter / 1000.0
                await self.send(
                    ws,
                    [
                        2,
                        "meter",
                        "MeterValues",
                        {
                            "connectorId": self.config.connector_id,
                            "meterValue": [
                                {
                                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S") + "Z",
                                    "sampledValue": [
                                        {
                                            "value": f"{next_kwh:.3f}",
                                            "measurand": "Energy.Active.Import.Register",
                                            "unit": "kWh",
                                            "context": "Sample.Clock",
                                        }
                                    ],
                                }
                            ],
                        },
                    ],
                )
                self.state.last_message = "MeterValues"
                await self.recv(ws)
                last_mv = time.monotonic()
        return next_meter

    async def run_meter_reporting_loop(
        self,
        ws: Any,
        tx_id: int | None,
        meter_start: int,
        step_avg: float,
        stop_event: asyncio.Event,
    ) -> int:
        meter = meter_start
        start_time = time.monotonic()
        while time.monotonic() - start_time < self.config.duration:
            if stop_event.is_set():
                break
            inc = self.jitter(step_avg)
            meter += max(1, int(inc))
            meter_kwh = meter / 1000.0
            current_amp = self.jitter(self.config.amperage)
            await self.send(
                ws,
                [
                    2,
                    "meter",
                    "MeterValues",
                    {
                        "connectorId": self.config.connector_id,
                        "transactionId": tx_id,
                        "meterValue": [
                            {
                                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S") + "Z",
                                "sampledValue": [
                                    {
                                        "value": f"{meter_kwh:.3f}",
                                        "measurand": "Energy.Active.Import.Register",
                                        "unit": "kWh",
                                        "context": "Sample.Periodic",
                                    },
                                    {
                                        "value": f"{current_amp:.3f}",
                                        "measurand": "Current.Import",
                                        "unit": "A",
                                        "context": "Sample.Periodic",
                                    },
                                ],
                            }
                        ],
                    },
                ],
            )
            self.state.last_message = "MeterValues"
            await asyncio.sleep(self.meter_interval)
        return meter

    async def run_charging_session_loop(self, ws: Any) -> bool:
        meter_start, step_avg = self.metering_plan()
        meter_start = await self.pre_charge(ws, meter_start, step_avg)
        tx_id = await self.start_transaction(ws, meter_start)
        stop_event, reset_event, listener = self.setup_command_listener(ws)

        try:
            meter = await self.run_meter_reporting_loop(ws, tx_id, meter_start, step_avg, stop_event)
        finally:
            listener.cancel()
            try:
                await listener
            except asyncio.CancelledError:
                pass

        await self.stop_transaction(ws, tx_id, meter)
        await self.idle(ws, meter, step_avg, stop_event)
        return reset_event.is_set()

    async def start_transaction(self, ws: Any, meter_start: int) -> int | None:
        await self.send(
            ws,
            [
                2,
                "start",
                "StartTransaction",
                {
                    "connectorId": self.config.connector_id,
                    "idTag": self.config.rfid,
                    "meterStart": meter_start,
                    "vin": self.config.vin,
                },
            ],
        )
        self.state.last_message = "StartTransaction"
        resp = await self.recv(ws)
        try:
            parsed = json.loads(resp)
            if not isinstance(parsed, list) or len(parsed) < 3:
                raise ValueError("StartTransaction response is not a list")
            response_payload = parsed[2]
            if not isinstance(response_payload, dict):
                raise ValueError("StartTransaction response payload is invalid")
            tx_id = response_payload.get("transactionId")
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self.log(
                "Warning: Could not parse transactionId from StartTransaction "
                f"response: {exc}; raw response={resp}"
            )
            tx_id = None
        self.state.last_status = "Running"
        self.state.phase = "Charging"
        return tx_id

    async def stop_transaction(self, ws: Any, tx_id: int | None, meter: int) -> None:
        await self.send(
            ws,
            [
                2,
                "stop",
                "StopTransaction",
                {
                    "transactionId": tx_id,
                    "idTag": self.config.rfid,
                    "meterStop": meter,
                },
            ],
        )
        self.state.last_message = "StopTransaction"
        self.state.phase = "Available"
        await self.recv(ws)

    async def idle(
        self,
        ws: Any,
        meter: int,
        step_avg: float,
        stop_event: asyncio.Event,
    ) -> None:
        idle_time = 20 if self.config.session_count == 1 else 60
        next_meter = meter
        last_mv = time.monotonic()
        start_idle = time.monotonic()
        while time.monotonic() - start_idle < idle_time and not stop_event.is_set():
            await self.send(ws, [2, "hb", "Heartbeat", {}])
            self.state.last_message = "Heartbeat"
            await self.recv(ws)
            await asyncio.sleep(self.meter_interval)
            if time.monotonic() - last_mv >= 30:
                idle_step = max(2, int(step_avg / 100))
                next_meter += random.randint(0, idle_step)
                next_kwh = next_meter / 1000.0
                await self.send(
                    ws,
                    [
                        2,
                        "meter",
                        "MeterValues",
                        {
                            "connectorId": self.config.connector_id,
                            "meterValue": [
                                {
                                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S") + "Z",
                                    "sampledValue": [
                                        {
                                            "value": f"{next_kwh:.3f}",
                                            "measurand": "Energy.Active.Import.Register",
                                            "unit": "kWh",
                                            "context": "Sample.Clock",
                                        }
                                    ],
                                }
                            ],
                        },
                    ],
                )
                self.state.last_message = "MeterValues"
                await self.recv(ws)
                last_mv = time.monotonic()

    async def close(self, ws: Any) -> None:
        await ws.close()
        self.log(f"Closed (code={ws.close_code}, reason={getattr(ws, 'close_reason', '')})")

    async def run(self) -> None:
        loop_count = 0
        while loop_count < self.config.session_count and self.state.running:
            ws = None
            reset_requested = False
            try:
                ws = await self.establish_connection()
                await self.perform_ocpp_handshake(ws)
                reset_requested = await self.run_charging_session_loop(ws)
            except websockets.ConnectionClosedError:
                self.state.last_status = "Reconnecting"
                self.state.phase = "Reconnecting"
                await asyncio.sleep(1)
                continue
            except Exception as exc:  # pragma: no cover - defensive programming
                self.state.last_error = str(exc)
                break
            finally:
                if ws is not None:
                    await self.close(ws)

            if reset_requested:
                continue

            loop_count += 1
            if self.config.session_count == float("inf"):
                continue

        self.state.last_status = "Stopped"
        self.state.running = False
        self.state.phase = "Stopped"
        self.state.stop_time = time.strftime("%Y-%m-%d %H:%M:%S")
        self.save_state()

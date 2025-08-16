import asyncio
import base64
import json
import random
import time
from dataclasses import dataclass
from typing import Optional
import threading

import websockets
from config.offline import requires_network

from . import store


@dataclass
class SimulatorConfig:
    """Configuration for a simulated charge point."""

    host: str = "127.0.0.1"
    ws_port: int = 8000
    rfid: str = "FFFFFFFF"
    vin: str = ""
    # WebSocket path for the charge point. Defaults to just the charger ID at the root.
    cp_path: str = "CPX/"
    duration: int = 600
    kw_min: float = 30.0
    kw_max: float = 60.0
    interval: float = 5.0
    pre_charge_delay: float = 10.0
    repeat: bool = False
    username: Optional[str] = None
    password: Optional[str] = None


class ChargePointSimulator:
    """Lightweight simulator for a single OCPP 1.6 charge point."""

    def __init__(self, config: SimulatorConfig) -> None:
        self.config = config
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.status = "stopped"
        self._connected = threading.Event()
        self._connect_error = ""

    @requires_network
    async def _run_session(self) -> None:
        cfg = self.config
        uri = f"ws://{cfg.host}:{cfg.ws_port}/{cfg.cp_path}"
        headers = {}
        if cfg.username and cfg.password:
            userpass = f"{cfg.username}:{cfg.password}"
            b64 = base64.b64encode(userpass.encode()).decode()
            headers["Authorization"] = f"Basic {b64}"

        ws = None
        try:
            try:
                ws = await websockets.connect(
                    uri, subprotocols=["ocpp1.6"], extra_headers=headers
                )
            except Exception as exc:
                store.add_log(
                    cfg.cp_path,
                    f"Connection with subprotocol failed: {exc}",
                    log_type="simulator",
                )
                ws = await websockets.connect(uri, extra_headers=headers)

            store.add_log(
                cfg.cp_path,
                f"Connected (subprotocol={ws.subprotocol or 'none'})",
                log_type="simulator",
            )

            async def send(msg: str) -> None:
                try:
                    await ws.send(msg)
                except Exception:
                    self.status = "error"
                    raise
                store.add_log(cfg.cp_path, f"> {msg}", log_type="simulator")

            async def recv() -> str:
                try:
                    raw = await ws.recv()
                except Exception:
                    self.status = "error"
                    raise
                store.add_log(cfg.cp_path, f"< {raw}", log_type="simulator")
                return raw

            # handshake
            boot = json.dumps(
                [
                    2,
                    "boot",
                    "BootNotification",
                    {
                        "chargePointModel": "Simulator",
                        "chargePointVendor": "SimVendor",
                    },
                ]
            )
            await send(boot)
            try:
                resp = json.loads(await recv())
            except Exception:
                self.status = "error"
                raise
            status = resp[2].get("status")
            if status != "Accepted":
                if not self._connected.is_set():
                    self._connect_error = f"Boot status {status}"
                    self._connected.set()
                return

            await send(json.dumps([2, "auth", "Authorize", {"idTag": cfg.rfid}]))
            await recv()
            if not self._connected.is_set():
                self.status = "running"
                self._connect_error = "accepted"
                self._connected.set()
            if cfg.pre_charge_delay > 0:
                idle_start = time.monotonic()
                while time.monotonic() - idle_start < cfg.pre_charge_delay:
                    await send(
                        json.dumps(
                            [
                                2,
                                "status",
                                "StatusNotification",
                                {
                                    "connectorId": 1,
                                    "errorCode": "NoError",
                                    "status": "Available",
                                },
                            ]
                        )
                    )
                    await recv()
                    await asyncio.sleep(cfg.interval)

            meter_start = random.randint(1000, 2000)
            await send(
                json.dumps(
                    [
                        2,
                        "start",
                        "StartTransaction",
                        {
                            "connectorId": 1,
                            "idTag": cfg.rfid,
                            "meterStart": meter_start,
                            "vin": cfg.vin,
                        },
                    ]
                )
            )
            try:
                resp = json.loads(await recv())
            except Exception:
                self.status = "error"
                raise
            tx_id = resp[2].get("transactionId")

            meter = meter_start
            steps = max(1, int(cfg.duration / cfg.interval))
            target_kwh = cfg.kw_max * random.uniform(0.9, 1.1)
            step_avg = (target_kwh * 1000) / steps

            start_time = time.monotonic()
            while time.monotonic() - start_time < cfg.duration:
                if self._stop_event.is_set():
                    break
                inc = random.gauss(step_avg, step_avg * 0.05)
                meter += max(1, int(inc))
                meter_kw = meter / 1000.0
                await send(
                    json.dumps(
                        [
                            2,
                            "meter",
                            "MeterValues",
                            {
                                "connectorId": 1,
                                "transactionId": tx_id,
                                "meterValue": [
                                    {
                                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                        "sampledValue": [
                                            {
                                                "value": f"{meter_kw:.3f}",
                                                "measurand": "Energy.Active.Import.Register",
                                                "unit": "kW",
                                            }
                                        ],
                                    }
                                ],
                            },
                        ]
                    )
                )
                await recv()
                await asyncio.sleep(cfg.interval)

            await send(
                json.dumps(
                    [
                        2,
                        "stop",
                        "StopTransaction",
                        {
                            "transactionId": tx_id,
                            "idTag": cfg.rfid,
                            "meterStop": meter,
                        },
                    ]
                )
            )
            await recv()
        except Exception as exc:
            if not self._connected.is_set():
                self._connect_error = str(exc)
                self._connected.set()
            self.status = "error"
            self._stop_event.set()
            if isinstance(exc, websockets.exceptions.ConnectionClosed):
                return
            raise
        finally:
            if ws is not None:
                await ws.close()
                store.add_log(
                    cfg.cp_path,
                    f"Closed (code={ws.close_code}, reason={getattr(ws, 'close_reason', '')})",
                    log_type="simulator",
                )

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._run_session()
            except asyncio.CancelledError:
                break
            except Exception:
                # wait briefly then retry
                await asyncio.sleep(1)
                continue
            if not self.config.repeat:
                break

    def start(self) -> tuple[bool, str, str]:
        if self._thread and self._thread.is_alive():
            return (
                False,
                "already running",
                str(store._file_path(self.config.cp_path, log_type="simulator")),
            )

        self._stop_event.clear()
        self.status = "starting"
        self._connected.clear()
        self._connect_error = ""

        def _runner() -> None:
            asyncio.run(self._run())

        self._thread = threading.Thread(target=_runner, daemon=True)
        self._thread.start()

        log_file = str(store._file_path(self.config.cp_path, log_type="simulator"))
        if not self._connected.wait(15):
            self.status = "error"
            return False, "Connection timeout", log_file
        if self._connect_error == "accepted":
            self.status = "running"
            return True, "Connection accepted", log_file
        self.status = "error"
        return False, f"Connection failed: {self._connect_error}", log_file

    async def stop(self) -> None:
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
            await asyncio.to_thread(self._thread.join)
            self._thread = None
            self._stop_event = threading.Event()
        self.status = "stopped"


__all__ = ["SimulatorConfig", "ChargePointSimulator"]

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
    # WebSocket path for the charge point. Defaults to just the charger ID at the root.
    cp_path: str = "CPX/"
    duration: int = 600
    kwh_min: float = 30.0
    kwh_max: float = 60.0
    interval: float = 5.0
    pre_charge_delay: float = 0.0
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

        try:
            async with websockets.connect(
                uri, subprotocols=["ocpp1.6"], extra_headers=headers
            ) as ws:
                async def send(msg: str) -> None:
                    await ws.send(msg)
                    store.add_log(cfg.cp_path, f"> {msg}")

                async def recv() -> str:
                    raw = await ws.recv()
                    store.add_log(cfg.cp_path, f"< {raw}")
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
                resp = json.loads(await recv())
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
                            },
                        ]
                    )
                )
                resp = json.loads(await recv())
                tx_id = resp[2].get("transactionId")

            meter = meter_start
            steps = max(1, int(cfg.duration / cfg.interval))
            step_min = max(1, int((cfg.kwh_min * 1000) / steps))
            step_max = max(1, int((cfg.kwh_max * 1000) / steps))

            start_time = time.monotonic()
            while time.monotonic() - start_time < cfg.duration:
                if self._stop_event.is_set():
                    break
                meter += random.randint(step_min, step_max)
                meter_kwh = meter / 1000.0
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
                                                "value": f"{meter_kwh:.3f}",
                                                "measurand": "Energy.Active.Import.Register",
                                                "unit": "kWh",
                                            }
                                        ],
                                    }
                                ],
                            },
                        ]
                    )
                )
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
            self._stop_event.set()
            if isinstance(exc, websockets.exceptions.ConnectionClosed):
                return
            raise

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
            return False, "already running", str(store._file_path(self.config.cp_path))

        self._stop_event.clear()
        self.status = "starting"
        self._connected.clear()
        self._connect_error = ""

        def _runner() -> None:
            asyncio.run(self._run())

        self._thread = threading.Thread(target=_runner, daemon=True)
        self._thread.start()

        log_file = str(store._file_path(self.config.cp_path))
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

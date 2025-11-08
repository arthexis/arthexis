import asyncio
import base64
import json
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional
import threading

import websockets
from config.offline import requires_network

from . import store
from .websocket_headers import connect_headers_kwargs


@dataclass
class SimulatorConfig:
    """Configuration for a simulated charge point."""

    host: str = "127.0.0.1"
    ws_port: Optional[int] = 8000
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
    serial_number: str = ""
    connector_id: int = 1
    configuration_keys: list[dict[str, object]] = field(default_factory=list)
    configuration_unknown_keys: list[str] = field(default_factory=list)


class ChargePointSimulator:
    """Lightweight simulator for a single OCPP 1.6 charge point."""

    def __init__(self, config: SimulatorConfig) -> None:
        self.config = config
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._door_open_event = threading.Event()
        self.status = "stopped"
        self._connected = threading.Event()
        self._connect_error = ""
        self._availability_state = "Operative"
        self._pending_availability: Optional[str] = None
        self._in_transaction = False

    def trigger_door_open(self) -> None:
        """Queue a DoorOpen status notification for the simulator."""

        self._door_open_event.set()

    async def _maybe_send_door_event(self, send, recv) -> None:
        if not self._door_open_event.is_set():
            return
        self._door_open_event.clear()
        cfg = self.config
        store.add_log(
            cfg.cp_path,
            "Sending DoorOpen StatusNotification",
            log_type="simulator",
        )
        event_id = uuid.uuid4().hex
        await send(
            json.dumps(
                [
                    2,
                    f"door-open-{event_id}",
                    "StatusNotification",
                    {
                        "connectorId": cfg.connector_id,
                        "errorCode": "DoorOpen",
                        "status": "Faulted",
                    },
                ]
            )
        )
        await recv()
        await send(
            json.dumps(
                [
                    2,
                    f"door-closed-{event_id}",
                    "StatusNotification",
                    {
                        "connectorId": cfg.connector_id,
                        "errorCode": "NoError",
                        "status": "Available",
                    },
                ]
            )
        )
        await recv()

    async def _send_status_notification(self, send, recv, status: str) -> None:
        cfg = self.config
        await send(
            json.dumps(
                [
                    2,
                    f"status-{uuid.uuid4().hex}",
                    "StatusNotification",
                    {
                        "connectorId": cfg.connector_id,
                        "errorCode": "NoError",
                        "status": status,
                    },
                ]
            )
        )
        await recv()

    async def _wait_until_operative(self, send, recv) -> bool:
        cfg = self.config
        delay = cfg.interval if cfg.interval > 0 else 1.0
        while self._availability_state != "Operative" and not self._stop_event.is_set():
            await send(
                json.dumps(
                    [
                        2,
                        f"hb-wait-{uuid.uuid4().hex}",
                        "Heartbeat",
                        {},
                    ]
                )
            )
            try:
                await recv()
            except Exception:
                return False
            await self._maybe_send_door_event(send, recv)
            await asyncio.sleep(delay)
        return self._availability_state == "Operative" and not self._stop_event.is_set()

    async def _handle_change_availability(self, message_id: str, payload, send, recv) -> None:
        cfg = self.config
        requested_type = str((payload or {}).get("type") or "").strip()
        connector_raw = (payload or {}).get("connectorId")
        try:
            connector_value = int(connector_raw)
        except (TypeError, ValueError):
            connector_value = None
        if connector_value in (None, 0):
            connector_value = 0
        valid_connectors = {0, cfg.connector_id}
        send_status: Optional[str] = None
        status_result = "Rejected"
        if requested_type in {"Operative", "Inoperative"} and connector_value in valid_connectors:
            if requested_type == "Inoperative":
                if self._in_transaction:
                    self._pending_availability = "Inoperative"
                    status_result = "Scheduled"
                else:
                    self._pending_availability = None
                    status_result = "Accepted"
                    if self._availability_state != "Inoperative":
                        self._availability_state = "Inoperative"
                        send_status = "Unavailable"
            else:  # Operative
                self._pending_availability = None
                status_result = "Accepted"
                if self._availability_state != "Operative":
                    self._availability_state = "Operative"
                    send_status = "Available"
        response = [3, message_id, {"status": status_result}]
        await send(json.dumps(response))
        if send_status:
            await self._send_status_notification(send, recv, send_status)

    async def _handle_trigger_message(self, message_id: str, payload, send, recv) -> None:
        cfg = self.config
        payload = payload if isinstance(payload, dict) else {}
        requested = str(payload.get("requestedMessage") or "").strip()
        connector_raw = payload.get("connectorId")
        try:
            connector_value = int(connector_raw) if connector_raw is not None else None
        except (TypeError, ValueError):
            connector_value = None

        async def _send_follow_up(action: str, payload_obj: dict) -> None:
            await send(
                json.dumps(
                    [
                        2,
                        f"trigger-{uuid.uuid4().hex}",
                        action,
                        payload_obj,
                    ]
                )
            )
            await recv()

        status_result = "NotSupported"
        follow_up = None

        if requested == "BootNotification":
            status_result = "Accepted"

            async def _boot_notification() -> None:
                await _send_follow_up(
                    "BootNotification",
                    {
                        "chargePointVendor": "SimVendor",
                        "chargePointModel": "Simulator",
                        "serialNumber": cfg.serial_number,
                    },
                )

            follow_up = _boot_notification
        elif requested == "Heartbeat":
            status_result = "Accepted"

            async def _heartbeat() -> None:
                await _send_follow_up("Heartbeat", {})

            follow_up = _heartbeat
        elif requested == "StatusNotification":
            valid_connector = connector_value in (None, cfg.connector_id)
            if valid_connector:
                status_result = "Accepted"

                async def _status_notification() -> None:
                    status_label = (
                        "Available"
                        if self._availability_state == "Operative"
                        else "Unavailable"
                    )
                    await _send_follow_up(
                        "StatusNotification",
                        {
                            "connectorId": connector_value or cfg.connector_id,
                            "errorCode": "NoError",
                            "status": status_label,
                        },
                    )

                follow_up = _status_notification
            else:
                status_result = "Rejected"
        elif requested == "MeterValues":
            valid_connector = connector_value in (None, cfg.connector_id)
            if valid_connector:
                status_result = "Accepted"

                async def _meter_values() -> None:
                    await _send_follow_up(
                        "MeterValues",
                        {
                            "connectorId": connector_value or cfg.connector_id,
                            "meterValue": [
                                {
                                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    "sampledValue": [
                                        {
                                            "value": "0",
                                            "measurand": "Energy.Active.Import.Register",
                                            "unit": "kW",
                                        }
                                    ],
                                }
                            ],
                        },
                    )

                follow_up = _meter_values
            else:
                status_result = "Rejected"
        elif requested == "DiagnosticsStatusNotification":
            status_result = "Accepted"

            async def _diagnostics() -> None:
                await _send_follow_up(
                    "DiagnosticsStatusNotification",
                    {"status": "Idle"},
                )

            follow_up = _diagnostics
        elif requested == "FirmwareStatusNotification":
            status_result = "Accepted"

            async def _firmware() -> None:
                await _send_follow_up(
                    "FirmwareStatusNotification",
                    {"status": "Idle"},
                )

            follow_up = _firmware

        response = [3, message_id, {"status": status_result}]
        await send(json.dumps(response))
        if status_result == "Accepted" and follow_up:
            await follow_up()

    async def _handle_csms_call(self, msg, send, recv) -> bool:
        if not isinstance(msg, list) or not msg or msg[0] != 2:
            return False
        message_id = msg[1] if len(msg) > 1 else ""
        if not isinstance(message_id, str):
            message_id = str(message_id)
        action = msg[2]
        payload = msg[3] if len(msg) > 3 else {}
        if action == "ChangeAvailability":
            await self._handle_change_availability(message_id, payload, send, recv)
            return True
        if action == "GetConfiguration":
            await self._handle_get_configuration(message_id, payload, send)
            return True
        if action == "TriggerMessage":
            await self._handle_trigger_message(message_id, payload, send, recv)
            return True
        cfg = self.config
        action_name = str(action)
        store.add_log(
            cfg.cp_path,
            f"Received unsupported action '{action_name}', replying with CallError",
            log_type="simulator",
        )
        await send(
            json.dumps(
                [
                    4,
                    message_id,
                    "NotSupported",
                    f"Simulator does not implement {action_name}",
                    {},
                ]
            )
        )
        return True

    async def _handle_get_configuration(self, message_id: str, payload, send) -> None:
        cfg = self.config
        payload = payload if isinstance(payload, dict) else {}
        requested_keys_raw = payload.get("key")
        requested_keys: list[str] = []
        if isinstance(requested_keys_raw, (list, tuple)):
            for item in requested_keys_raw:
                if isinstance(item, str):
                    key_text = item.strip()
                else:
                    key_text = str(item).strip()
                if key_text:
                    requested_keys.append(key_text)

        configured_entries: list[dict[str, object]] = []
        for entry in cfg.configuration_keys:
            if not isinstance(entry, dict):
                continue
            key_raw = entry.get("key")
            key_text = str(key_raw).strip() if key_raw is not None else ""
            if not key_text:
                continue
            if requested_keys and key_text not in requested_keys:
                continue
            value = entry.get("value")
            readonly = entry.get("readonly")
            payload_entry: dict[str, object] = {"key": key_text}
            if value is not None:
                payload_entry["value"] = str(value)
            if readonly is not None:
                payload_entry["readonly"] = bool(readonly)
            configured_entries.append(payload_entry)

        unknown_keys: list[str] = []
        for key in cfg.configuration_unknown_keys:
            key_text = str(key).strip()
            if not key_text:
                continue
            if requested_keys and key_text not in requested_keys:
                continue
            if key_text not in unknown_keys:
                unknown_keys.append(key_text)

        if requested_keys:
            matched = {entry["key"] for entry in configured_entries}
            for key in requested_keys:
                if key not in matched and key not in unknown_keys:
                    unknown_keys.append(key)

        response_payload: dict[str, object] = {}
        if configured_entries:
            response_payload["configurationKey"] = configured_entries
        if unknown_keys:
            response_payload["unknownKey"] = unknown_keys
        await send(json.dumps([3, message_id, response_payload]))

    @requires_network
    async def _run_session(self) -> None:
        cfg = self.config
        if cfg.ws_port:
            uri = f"ws://{cfg.host}:{cfg.ws_port}/{cfg.cp_path}"
        else:
            uri = f"ws://{cfg.host}/{cfg.cp_path}"
        headers: dict[str, str] = {}
        if cfg.username and cfg.password:
            userpass = f"{cfg.username}:{cfg.password}"
            b64 = base64.b64encode(userpass.encode()).decode()
            headers["Authorization"] = f"Basic {b64}"

        connect_kwargs = connect_headers_kwargs(headers)

        ws = None
        try:
            try:
                ws = await websockets.connect(
                    uri, subprotocols=["ocpp1.6"], **connect_kwargs
                )
            except Exception as exc:
                store.add_log(
                    cfg.cp_path,
                    f"Connection with subprotocol failed: {exc}",
                    log_type="simulator",
                )
                ws = await websockets.connect(uri, **connect_kwargs)

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
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=60)
                    except asyncio.TimeoutError:
                        self.status = "stopped"
                        self._stop_event.set()
                        store.add_log(
                            cfg.cp_path,
                            "Timeout waiting for response from charger",
                            log_type="simulator",
                        )
                        raise
                    except websockets.exceptions.ConnectionClosed:
                        self.status = "stopped"
                        self._stop_event.set()
                        raise
                    except Exception:
                        self.status = "error"
                        raise
                    store.add_log(cfg.cp_path, f"< {raw}", log_type="simulator")
                    try:
                        parsed = json.loads(raw)
                    except Exception:
                        return raw
                    handled = await self._handle_csms_call(parsed, send, recv)
                    if handled:
                        continue
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
                        "serialNumber": cfg.serial_number,
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
            await self._maybe_send_door_event(send, recv)
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
                                    "connectorId": cfg.connector_id,
                                    "errorCode": "NoError",
                                    "status": (
                                        "Available"
                                        if self._availability_state == "Operative"
                                        else "Unavailable"
                                    ),
                                },
                            ]
                        )
                    )
                    await recv()
                    await send(json.dumps([2, "hb", "Heartbeat", {}]))
                    await recv()
                    await send(
                        json.dumps(
                            [
                                2,
                                "meter",
                                "MeterValues",
                                {
                                    "connectorId": cfg.connector_id,
                                    "meterValue": [
                                        {
                                            "timestamp": time.strftime(
                                                "%Y-%m-%dT%H:%M:%SZ"
                                            ),
                                            "sampledValue": [
                                                {
                                                    "value": "0",
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
                    await self._maybe_send_door_event(send, recv)
                    await asyncio.sleep(cfg.interval)

            if not await self._wait_until_operative(send, recv):
                return
            meter_start = random.randint(1000, 2000)
            await send(
                json.dumps(
                    [
                        2,
                        "start",
                        "StartTransaction",
                        {
                            "connectorId": cfg.connector_id,
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
            self._in_transaction = True

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
                                "connectorId": cfg.connector_id,
                                "transactionId": tx_id,
                                "meterValue": [
                                    {
                                        "timestamp": time.strftime(
                                            "%Y-%m-%dT%H:%M:%SZ"
                                        ),
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
                await self._maybe_send_door_event(send, recv)
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
            await self._maybe_send_door_event(send, recv)
            self._in_transaction = False
            if self._pending_availability:
                pending = self._pending_availability
                self._pending_availability = None
                self._availability_state = pending
                status_label = "Available" if pending == "Operative" else "Unavailable"
                await self._send_status_notification(send, recv, status_label)
        except asyncio.TimeoutError:
            if not self._connected.is_set():
                self._connect_error = "Timeout waiting for response"
                self._connected.set()
            self.status = "stopped"
            self._stop_event.set()
            return
        except websockets.exceptions.ConnectionClosed as exc:
            if not self._connected.is_set():
                self._connect_error = str(exc)
                self._connected.set()
            # The charger closed the connection; mark the simulator as
            # terminated rather than erroring so the status reflects that it
            # was stopped remotely.
            self.status = "stopped"
            self._stop_event.set()
            store.add_log(
                cfg.cp_path,
                f"Disconnected by charger (code={getattr(exc, 'code', '')})",
                log_type="simulator",
            )
            return
        except Exception as exc:
            if not self._connected.is_set():
                self._connect_error = str(exc)
                self._connected.set()
            self.status = "error"
            self._stop_event.set()
            raise
        finally:
            self._in_transaction = False
            if ws is not None:
                await ws.close()
                store.add_log(
                    cfg.cp_path,
                    f"Closed (code={ws.close_code}, reason={getattr(ws, 'close_reason', '')})",
                    log_type="simulator",
                )

    async def _run(self) -> None:
        try:
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
        finally:
            for key, sim in list(store.simulators.items()):
                if sim is self:
                    store.simulators.pop(key, None)
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
        self._door_open_event.clear()

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
        if "Timeout" in self._connect_error:
            self.status = "stopped"
        else:
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

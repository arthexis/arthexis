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

from apps.ocpp import store
from apps.ocpp.utils import resolve_ws_scheme
from apps.simulators.network import validate_simulator_endpoint


def _ocpp_subprotocol_16j() -> str:
    from apps.ocpp.consumers.constants import OCPP_SUBPROTOCOL_16J

    return OCPP_SUBPROTOCOL_16J


class UnsupportedMessageError(RuntimeError):
    """Raised when the simulator receives a CSMS message it does not support."""


@dataclass
class SimulatorConfig:
    """Configuration for a simulated charge point."""

    host: str = "127.0.0.1"
    ws_port: Optional[int] = 8000
    ws_scheme: Optional[str] = None
    use_tls: Optional[bool] = None
    allow_private_network: bool = False
    rfid: str = "FFFFFFFF"
    vin: str = ""
    # WebSocket path for the charge point. Defaults to just the charger ID at the root.
    cp_path: str = "CPX/"
    duration: int = 600
    average_kwh: float = 60.0
    amperage: float = 90.0
    interval: float = 5.0
    pre_charge_delay: float = 10.0
    repeat: bool = False
    cp_idx: int = 1
    start_delay: float = 0.0
    meter_interval: float = 5.0
    reconnect_slots: str | None = None
    demo_mode: bool = False
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
        self._unsupported_message = False
        self._unsupported_message_reason = ""
        self._last_ws_subprotocol: Optional[str] = None
        self._last_close_code: Optional[int] = None
        self._last_close_reason: str | None = None
        self._ws = None

    def trigger_door_open(self) -> None:
        """Queue a DoorOpen status notification for the simulator."""

        self._door_open_event.set()

    def _set_status(self, status: str) -> None:
        """Update the simulator status in one place."""

        self.status = status

    def _signal_stop(self, status: str) -> None:
        """Mark the simulator as stopped and set the shared stop event."""

        self._set_status(status)
        self._stop_event.set()

    def _mark_connected(self, result: str, *, status: Optional[str] = None) -> None:
        """Publish the connection result for start-up coordination."""

        if status is not None:
            self._set_status(status)
        if not self._connected.is_set():
            self._connect_error = result
            self._connected.set()

    async def _send(self, message: str) -> None:
        """Send a websocket frame and log it.

        Parameters:
            message: Serialized websocket payload to send.

        Raises:
            RuntimeError: If no websocket is connected for the session.
            Exception: Re-raises websocket send failures after updating status.
        """

        if self._ws is None:
            raise RuntimeError("Simulator websocket is not connected.")
        try:
            await self._ws.send(message)
        except Exception:
            self._set_status("error")
            raise
        store.add_log(self.config.cp_path, f"> {message}", log_type="simulator")

    async def _recv(self) -> str:
        """Receive the next non-CSMS-call websocket frame.

        Returns:
            str: The next raw websocket frame that is not handled internally.

        Raises:
            TimeoutError: When the websocket does not produce a frame in time.
            UnsupportedMessageError: When the CSMS sends an unsupported CALL.
            Exception: Re-raises websocket receive failures after updating status.
        """

        if self._ws is None:
            raise RuntimeError("Simulator websocket is not connected.")
        while True:
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=60)
            except TimeoutError:
                self._signal_stop("stopped")
                store.add_log(
                    self.config.cp_path,
                    "Timeout waiting for response from charger",
                    log_type="simulator",
                )
                raise
            except websockets.exceptions.ConnectionClosed:
                self._signal_stop("stopped")
                raise
            except Exception:
                self._set_status("error")
                raise
            store.add_log(self.config.cp_path, f"< {raw}", log_type="simulator")
            try:
                parsed = json.loads(raw)
            except Exception:
                return raw
            handled = await self._handle_csms_call(parsed, self._send, self._recv)
            if handled:
                if self._unsupported_message:
                    raise UnsupportedMessageError(self._unsupported_message_reason)
                continue
            return raw

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
                        "chargePointSerialNumber": cfg.serial_number,
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
        if len(msg) < 3:
            store.add_log(
                self.config.cp_path,
                "Malformed CALL frame received; ignoring",
                log_type="simulator",
            )
            if message_id:
                await send(
                    json.dumps(
                        [4, str(message_id), "ProtocolError", "Malformed CALL", {}]
                    )
                )
            return True
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
            f"Received unsupported action '{action_name}', terminating simulator",
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
        self._unsupported_message = True
        self._unsupported_message_reason = (
            f"Simulator does not implement {action_name}"
        )
        self.status = "error"
        self._stop_event.set()
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

    def _websocket_headers(self) -> dict[str, str]:
        """Build HTTP headers for websocket negotiation."""

        cfg = self.config
        if not (cfg.username and cfg.password):
            return {}
        userpass = f"{cfg.username}:{cfg.password}"
        b64 = base64.b64encode(userpass.encode()).decode()
        return {"Authorization": f"Basic {b64}"}

    def _build_websocket_uri(self, ws_scheme: str) -> str:
        """Build the websocket URI for the configured charge point."""

        cfg = self.config
        if cfg.ws_port:
            return f"{ws_scheme}://{cfg.host}:{cfg.ws_port}/{cfg.cp_path}"
        return f"{ws_scheme}://{cfg.host}/{cfg.cp_path}"

    async def _connect_websocket(self) -> object:
        """Negotiate the simulator websocket connection.

        Returns:
            object: Connected websocket client returned by ``websockets.connect``.

        Raises:
            Exception: Propagates the last connection error after exhausting retries.
        """

        cfg = self.config
        requested_subprotocol = _ocpp_subprotocol_16j()
        scheme = resolve_ws_scheme(ws_scheme=cfg.ws_scheme, use_tls=cfg.use_tls)
        fallback_scheme = "ws" if scheme == "wss" else "wss"
        candidate_schemes = [scheme]
        if fallback_scheme != scheme:
            candidate_schemes.append(fallback_scheme)

        if cfg.username and cfg.password:
            is_loopback = cfg.host in {"127.0.0.1", "localhost", "::1"}
            if scheme != "wss" and not is_loopback:
                raise ValueError("Basic auth requires TLS (wss) for non-loopback hosts")
            if not is_loopback:
                candidate_schemes = ["wss"]

        validate_simulator_endpoint(
            cfg.host,
            cfg.ws_port,
            allow_private_network=cfg.allow_private_network,
        )

        connect_kwargs: dict[str, object] = {}
        headers = self._websocket_headers()
        if headers:
            connect_kwargs["additional_headers"] = headers

        last_error: Exception | None = None
        for ws_scheme in candidate_schemes:
            uri = self._build_websocket_uri(ws_scheme)
            ws = None
            try:
                for attempt in range(2):
                    try:
                        ws = await websockets.connect(
                            uri,
                            subprotocols=[requested_subprotocol],
                            **connect_kwargs,
                        )
                        break
                    except Exception as exc:
                        store.add_log(
                            cfg.cp_path,
                            (
                                "Connection with subprotocol failed "
                                f"({ws_scheme}, attempt {attempt + 1}): {exc}"
                            ),
                            log_type="simulator",
                        )
                        last_error = exc
                        if attempt < 1:
                            store.add_log(
                                cfg.cp_path,
                                "Retrying connection with subprotocol",
                                log_type="simulator",
                            )
                            await asyncio.sleep(0.1)
                if ws is not None:
                    return ws
                raise last_error or RuntimeError(
                    "Subprotocol connection attempts failed without a specific error."
                )
            except Exception:
                try:
                    ws = await websockets.connect(uri, **connect_kwargs)
                    return ws
                except Exception as inner_exc:
                    last_error = inner_exc
                    store.add_log(
                        cfg.cp_path,
                        f"Connection failed ({ws_scheme}): {inner_exc}",
                        log_type="simulator",
                    )
                    if ws_scheme != candidate_schemes[-1]:
                        store.add_log(
                            cfg.cp_path,
                            f"Retrying connection with scheme {candidate_schemes[-1]}",
                            log_type="simulator",
                        )
        raise last_error or RuntimeError("Unable to establish simulator websocket connection")

    async def _perform_boot_and_authorize_handshake(self) -> bool:
        """Run the boot notification and authorize exchange.

        Returns:
            bool: ``True`` when the charger accepts the handshake.
        """

        cfg = self.config
        boot = json.dumps(
            [
                2,
                "boot",
                "BootNotification",
                {
                    "chargePointModel": "Simulator",
                    "chargePointVendor": "SimVendor",
                    "chargePointSerialNumber": cfg.serial_number,
                },
            ]
        )
        await self._send(boot)
        try:
            resp = json.loads(await self._recv())
        except Exception:
            self._set_status("error")
            raise
        status = resp[2].get("status")
        if status != "Accepted":
            self._mark_connected(f"Boot status {status}")
            return False

        await self._send(json.dumps([2, "auth", "Authorize", {"idTag": cfg.rfid}]))
        await self._recv()
        await self._maybe_send_door_event(self._send, self._recv)
        self._mark_connected("accepted", status="running")
        return True

    async def _run_pre_charge_idle_loop(self) -> bool:
        """Emit idle status, heartbeat, and meter traffic before charging starts."""

        cfg = self.config
        if cfg.duration <= 0:
            self._signal_stop("stopped")
            return False
        if cfg.pre_charge_delay <= 0:
            return True

        idle_start = time.monotonic()
        while time.monotonic() - idle_start < cfg.pre_charge_delay:
            if self._stop_event.is_set():
                return False
            await self._send(
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
            await self._recv()
            await self._send(json.dumps([2, "hb", "Heartbeat", {}]))
            await self._recv()
            await self._send(
                json.dumps(
                    [
                        2,
                        "meter",
                        "MeterValues",
                        {
                            "connectorId": cfg.connector_id,
                            "meterValue": [
                                {
                                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    "sampledValue": [
                                        {
                                            "value": "0",
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
            await self._recv()
            await self._maybe_send_door_event(self._send, self._recv)
            await asyncio.sleep(cfg.interval)
        return not self._stop_event.is_set()

    async def _start_transaction(self) -> tuple[int, object]:
        """Start a charging transaction and return the initial meter and tx id."""

        cfg = self.config
        meter_start = random.randint(1000, 2000)
        await self._send(
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
            resp = json.loads(await self._recv())
        except Exception:
            self._set_status("error")
            raise
        self._in_transaction = True
        return meter_start, resp[2].get("transactionId")

    async def _run_metering_loop(self, *, meter_start: int, tx_id: object) -> tuple[bool, int]:
        """Send periodic meter values until the configured duration elapses."""

        cfg = self.config
        meter = meter_start
        steps = max(1, int(cfg.duration / cfg.interval))

        def _jitter(value: float) -> float:
            return value * random.uniform(0.95, 1.05)

        target_kwh = _jitter(cfg.average_kwh)
        step_avg = (target_kwh * 1000) / steps if steps else target_kwh * 1000

        start_time = time.monotonic()
        while time.monotonic() - start_time < cfg.duration:
            if self._stop_event.is_set():
                return False, meter
            inc = _jitter(step_avg)
            meter += max(1, int(inc))
            meter_kwh = meter / 1000.0
            amperage = _jitter(cfg.amperage)
            await self._send(
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
                                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    "sampledValue": [
                                        {
                                            "value": f"{meter_kwh:.3f}",
                                            "measurand": "Energy.Active.Import.Register",
                                            "unit": "kWh",
                                        },
                                        {
                                            "value": f"{amperage:.3f}",
                                            "measurand": "Current.Import",
                                            "unit": "A",
                                        },
                                    ],
                                }
                            ],
                        },
                    ]
                )
            )
            await self._recv()
            await self._maybe_send_door_event(self._send, self._recv)
            await asyncio.sleep(cfg.interval)
        return True, meter

    async def _finalize_transaction(self, tx_id: object, meter_stop: int) -> None:
        """Send the stop transaction frame and apply deferred cleanup."""

        await self._send(
            json.dumps(
                [
                    2,
                    "stop",
                    "StopTransaction",
                    {
                        "transactionId": tx_id,
                        "idTag": self.config.rfid,
                        "meterStop": meter_stop,
                    },
                ]
            )
        )
        await self._recv()
        await self._maybe_send_door_event(self._send, self._recv)
        self._in_transaction = False
        if self._pending_availability:
            pending = self._pending_availability
            self._pending_availability = None
            self._availability_state = pending
            status_label = "Available" if pending == "Operative" else "Unavailable"
            await self._send_status_notification(self._send, self._recv, status_label)

    @requires_network
    async def _run_session(self) -> None:
        self._last_ws_subprotocol = None
        self._last_close_code = None
        self._last_close_reason = None
        clean_exit = False
        abnormal_disconnect = False
        stop_requested = False
        cfg = self.config
        try:
            self._unsupported_message = False
            self._unsupported_message_reason = ""
            self._ws = await self._connect_websocket()
            negotiated_subprotocol = self._ws.subprotocol
            store.add_log(
                cfg.cp_path,
                f"Connected (subprotocol={negotiated_subprotocol or 'none'})",
                log_type="simulator",
            )
            self._last_ws_subprotocol = negotiated_subprotocol
            if not await self._perform_boot_and_authorize_handshake():
                return
            if not await self._run_pre_charge_idle_loop():
                stop_requested = self._stop_event.is_set()
                clean_exit = stop_requested
                return
            if not await self._wait_until_operative(self._send, self._recv):
                stop_requested = self._stop_event.is_set()
                clean_exit = stop_requested
                return
            meter_start, tx_id = await self._start_transaction()
            completed, meter_stop = await self._run_metering_loop(
                meter_start=meter_start,
                tx_id=tx_id,
            )
            stop_requested = not completed
            await self._finalize_transaction(tx_id, meter_stop)
            clean_exit = True
        except UnsupportedMessageError:
            self._mark_connected("Unsupported CSMS message")
            self._signal_stop("error")
            return
        except TimeoutError:
            abnormal_disconnect = True
            self._mark_connected("Timeout waiting for response")
            self._signal_stop("stopped")
            return
        except websockets.exceptions.ConnectionClosed as exc:
            abnormal_disconnect = True
            self._mark_connected(str(exc))
            # The charger closed the connection; mark the simulator as
            # terminated rather than erroring so the status reflects that it
            # was stopped remotely.
            self._signal_stop("stopped")
            store.add_log(
                cfg.cp_path,
                f"Disconnected by charger (code={getattr(exc, 'code', '')})",
                log_type="simulator",
            )
            return
        except Exception as exc:
            self._mark_connected(str(exc))
            self._signal_stop("error")
            raise
        finally:
            self._in_transaction = False
            ws = self._ws
            self._ws = None
            if ws is not None:
                await ws.close()
                close_code = ws.close_code
                is_clean_exit = (
                    clean_exit
                    or stop_requested
                    or (
                        self.status == "stopped"
                        and self._connect_error == "accepted"
                        and not abnormal_disconnect
                    )
                )
                if is_clean_exit and close_code in (None, 1006, 1011):
                    close_code = 1000
                self._last_close_code = close_code
                self._last_close_reason = getattr(ws, "close_reason", None)
                store.add_log(
                    cfg.cp_path,
                    f"Closed (code={close_code}, reason={getattr(ws, 'close_reason', '')})",
                    log_type="simulator",
                )
            if not self._stop_event.is_set():
                self._set_status("stopped")

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
        self._unsupported_message = False
        self._unsupported_message_reason = ""

        def _runner() -> None:
            asyncio.run(self._run())

        self._thread = threading.Thread(target=_runner, daemon=True)
        self._thread.start()

        log_file = str(store._file_path(self.config.cp_path, log_type="simulator"))
        if not self._connected.wait(15):
            self.status = "error"
            self._stop_event.set()
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=1)
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


__all__ = ["ChargePointSimulator", "SimulatorConfig"]

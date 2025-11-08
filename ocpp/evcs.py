"""Advanced OCPP charge point simulator.

This module is based on a more feature rich simulator used in the
``projects/ocpp/evcs.py`` file of the upstream project.  The original module
contains a large amount of functionality for driving one or more simulated
charge points, handling remote commands and persisting state.  The version
included with this repository previously exposed only a very small subset of
those features.  For the purposes of the tests in this kata we mirror the
behaviour of the upstream implementation in a lightweight, dependency free
fashion.

Only the portions that are useful for automated tests are implemented here.
The web based user interface present in the original file relies on additional
helpers and the Bottle framework.  To keep the module self contained and
importable in the test environment those parts are intentionally omitted.

The simulator exposes two high level helpers:

``simulate``
    Entry point used by administrative tasks to spawn one or more charge point
    simulations.  It can operate either synchronously or return a coroutine
    that can be awaited by the caller.

``simulate_cp``
    Coroutine that performs the actual OCPP exchange for a single charge point.
    It implements features such as boot notification, authorisation,
    meter‑value reporting, remote stop handling and optional pre‑charge delay.

In addition a small amount of state is persisted to ``simulator.json`` inside
the ``ocpp`` package.  The state tracking is intentionally simple but mirrors
the behaviour of the original code which recorded the last command executed and
whether the simulator was currently running.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import websockets
from . import store
from .websocket_headers import connect_headers_kwargs

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def parse_repeat(repeat: object) -> float:
    """Return the number of times a session should be repeated.

    The original implementation accepted a variety of inputs.  ``True`` or one
    of the strings ``"forever"``/``"infinite"`` result in an infinite loop.  A
    positive integer value indicates the exact number of sessions and any other
    value defaults to ``1``.
    """

    if repeat is True or (
        isinstance(repeat, str)
        and repeat.lower() in {"true", "forever", "infinite", "loop"}
    ):
        return float("inf")

    try:
        n = int(repeat)  # type: ignore[arg-type]
    except Exception:
        return 1
    return n if n > 0 else 1


def _thread_runner(target, *args, **kwargs) -> None:
    """Run ``target`` in a fresh asyncio loop inside a thread.

    The websockets library requires a running event loop.  When multiple charge
    points are simulated concurrently we spawn one thread per charge point and
    execute the async coroutine in its own event loop.
    """

    try:
        asyncio.run(target(*args, **kwargs))
    except Exception as exc:  # pragma: no cover - defensive programming
        print(f"[Simulator:thread] Exception: {exc}")


def _unique_cp_path(cp_path: str, idx: int, total_threads: int) -> str:
    """Return a unique charger path when multiple threads are used."""

    if total_threads == 1:
        return cp_path
    tag = secrets.token_hex(2).upper()  # four hex digits
    return f"{cp_path}-{tag}"


# ---------------------------------------------------------------------------
# Simulator state handling
# ---------------------------------------------------------------------------


@dataclass
class SimulatorState:
    running: bool = False
    last_status: str = ""
    last_command: Optional[str] = None
    last_error: str = ""
    last_message: str = ""
    phase: str = ""
    start_time: Optional[str] = None
    stop_time: Optional[str] = None
    params: Dict[str, object] | None = None


_simulators: Dict[int, SimulatorState] = {
    1: SimulatorState(),
    2: SimulatorState(),
}

# Persist state in the package directory so consecutive runs can load it.
STATE_FILE = Path(__file__).with_name("simulator.json")


def _load_state_file() -> Dict[str, Dict[str, object]]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text("utf-8"))
        except Exception:  # pragma: no cover - best effort load
            return {}
    return {}


def _save_state_file(states: Dict[int, SimulatorState]) -> None:
    try:  # pragma: no cover - best effort persistence
        data = {
            str(k): {
                "running": v.running,
                "last_status": v.last_status,
                "last_command": v.last_command,
                "last_error": v.last_error,
                "last_message": v.last_message,
                "phase": v.phase,
                "start_time": v.start_time,
                "stop_time": v.stop_time,
                "params": v.params or {},
            }
            for k, v in states.items()
        }
        STATE_FILE.write_text(json.dumps(data))
    except Exception:
        pass


# Load persisted state at import time
for key, val in _load_state_file().items():  # pragma: no cover - simple load
    try:
        _simulators[int(key)].__dict__.update(val)
    except Exception:
        continue


# ---------------------------------------------------------------------------
# Simulation logic
# ---------------------------------------------------------------------------


async def simulate_cp(
    cp_idx: int,
    host: str,
    ws_port: Optional[int],
    rfid: str,
    vin: str,
    cp_path: str,
    serial_number: str,
    connector_id: int,
    duration: int,
    kw_min: float,
    kw_max: float,
    pre_charge_delay: float,
    session_count: float,
    interval: float = 5.0,
    username: Optional[str] = None,
    password: Optional[str] = None,
    *,
    sim_state: SimulatorState | None = None,
) -> None:
    """Simulate one charge point session.

    This coroutine closely mirrors the behaviour of the upstream project.  A
    charge point connects to the central system, performs a boot notification,
    authorisation and transaction loop while periodically reporting meter
    values.  The function is resilient to remote stop requests and reconnects
    if the server closes the connection.
    """

    if ws_port:
        uri = f"ws://{host}:{ws_port}/{cp_path}"
    else:
        uri = f"ws://{host}/{cp_path}"
    headers: dict[str, str] = {}
    if username and password:
        userpass = f"{username}:{password}"
        b64 = base64.b64encode(userpass.encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {b64}"

    connect_kwargs = connect_headers_kwargs(headers)

    state = sim_state or _simulators.get(cp_idx + 1, _simulators[1])

    loop_count = 0
    while loop_count < session_count and state.running:
        ws = None
        reset_event: asyncio.Event | None = None
        try:
            try:
                ws = await websockets.connect(
                    uri, subprotocols=["ocpp1.6"], **connect_kwargs
                )
            except Exception as exc:
                store.add_log(
                    cp_path,
                    f"Connection with subprotocol failed: {exc}",
                    log_type="simulator",
                )
                ws = await websockets.connect(uri, **connect_kwargs)

            state.phase = "Connected"
            state.last_message = ""
            store.add_log(
                cp_path,
                f"Connected (subprotocol={ws.subprotocol or 'none'})",
                log_type="simulator",
            )

            async def _send(payload):
                text = json.dumps(payload)
                await ws.send(text)
                store.add_log(cp_path, f"> {text}", log_type="simulator")

            async def _recv():
                raw = await ws.recv()
                store.add_log(cp_path, f"< {raw}", log_type="simulator")
                return raw

            # listen for remote commands
            stop_event = asyncio.Event()
            reset_event = asyncio.Event()

            async def listen():
                try:
                    while True:
                        raw = await _recv()
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        if isinstance(msg, list) and msg and msg[0] == 2:
                            msg_id, action = msg[1], msg[2]
                            await _send([3, msg_id, {}])
                            if action == "RemoteStopTransaction":
                                state.last_message = "RemoteStopTransaction"
                                stop_event.set()
                            elif action == "Reset":
                                state.last_message = "Reset"
                                reset_event.set()
                                stop_event.set()
                except websockets.ConnectionClosed:
                    stop_event.set()

            await _send(
                [
                    2,
                    "boot",
                    "BootNotification",
                    {
                        "chargePointModel": "Simulator",
                        "chargePointVendor": "SimVendor",
                        "serialNumber": serial_number,
                    },
                ]
            )
            state.last_message = "BootNotification"
            await _recv()
            await _send([2, "auth", "Authorize", {"idTag": rfid}])
            state.last_message = "Authorize"
            await _recv()

            state.phase = "Available"

            meter_start = random.randint(1000, 2000)
            actual_duration = random.uniform(duration * 0.75, duration * 1.25)
            steps = max(1, int(actual_duration / interval))
            step_min = max(1, int((kw_min * 1000) / steps))
            step_max = max(1, int((kw_max * 1000) / steps))

            # optional pre‑charge delay while still sending heartbeats
            if pre_charge_delay > 0:
                start_delay = time.monotonic()
                next_meter = meter_start
                last_mv = time.monotonic()
                while time.monotonic() - start_delay < pre_charge_delay:
                    await _send([2, "hb", "Heartbeat", {}])
                    state.last_message = "Heartbeat"
                    await _recv()
                    await asyncio.sleep(5)
                    if time.monotonic() - last_mv >= 30:
                        idle_step = max(2, int(step_max / 100))
                        next_meter += random.randint(0, idle_step)
                        next_kw = next_meter / 1000.0
                        await _send(
                            [
                                2,
                                "meter",
                                "MeterValues",
                                {
                                    "connectorId": connector_id,
                                    "meterValue": [
                                        {
                                            "timestamp": time.strftime(
                                                "%Y-%m-%dT%H:%M:%S"
                                            )
                                            + "Z",
                                            "sampledValue": [
                                                {
                                                    "value": f"{next_kw:.3f}",
                                                    "measurand": "Energy.Active.Import.Register",
                                                    "unit": "kW",
                                                    "context": "Sample.Clock",
                                                }
                                            ],
                                        }
                                    ],
                                },
                            ]
                        )
                        state.last_message = "MeterValues"
                        await _recv()
                        last_mv = time.monotonic()

            await _send(
                [
                    2,
                    "start",
                    "StartTransaction",
                    {
                        "connectorId": connector_id,
                        "idTag": rfid,
                        "meterStart": meter_start,
                        "vin": vin,
                    },
                ]
            )
            state.last_message = "StartTransaction"
            resp = await _recv()
            tx_id = json.loads(resp)[2].get("transactionId")

            state.last_status = "Running"
            state.phase = "Charging"

            listener = asyncio.create_task(listen())

            meter = meter_start
            for _ in range(steps):
                if stop_event.is_set():
                    break
                meter += random.randint(step_min, step_max)
                meter_kw = meter / 1000.0
                await _send(
                    [
                        2,
                        "meter",
                        "MeterValues",
                        {
                            "connectorId": connector_id,
                            "transactionId": tx_id,
                            "meterValue": [
                                {
                                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")
                                    + "Z",
                                    "sampledValue": [
                                        {
                                            "value": f"{meter_kw:.3f}",
                                            "measurand": "Energy.Active.Import.Register",
                                            "unit": "kW",
                                            "context": "Sample.Periodic",
                                        }
                                    ],
                                }
                            ],
                        },
                    ]
                )
                state.last_message = "MeterValues"
                await asyncio.sleep(interval)

            listener.cancel()
            try:
                await listener
            except asyncio.CancelledError:
                pass

            await _send(
                [
                    2,
                    "stop",
                    "StopTransaction",
                    {
                        "transactionId": tx_id,
                        "idTag": rfid,
                        "meterStop": meter,
                    },
                ]
            )
            state.last_message = "StopTransaction"
            state.phase = "Available"
            await _recv()

            # Idle phase: heartbeats and idle meter values
            idle_time = 20 if session_count == 1 else 60
            next_meter = meter
            last_mv = time.monotonic()
            start_idle = time.monotonic()
            while time.monotonic() - start_idle < idle_time and not stop_event.is_set():
                await _send([2, "hb", "Heartbeat", {}])
                state.last_message = "Heartbeat"
                await asyncio.sleep(5)
                if time.monotonic() - last_mv >= 30:
                    idle_step = max(2, int(step_max / 100))
                    next_meter += random.randint(0, idle_step)
                    next_kw = next_meter / 1000.0
                    await _send(
                        [
                            2,
                            "meter",
                            "MeterValues",
                            {
                                "connectorId": connector_id,
                                "meterValue": [
                                    {
                                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")
                                        + "Z",
                                        "sampledValue": [
                                            {
                                                "value": f"{next_kw:.3f}",
                                                "measurand": "Energy.Active.Import.Register",
                                                "unit": "kW",
                                                "context": "Sample.Clock",
                                            }
                                        ],
                                    }
                                ],
                            },
                        ]
                    )
                    state.last_message = "MeterValues"
                    await _recv()
                    last_mv = time.monotonic()

        except websockets.ConnectionClosedError:
            state.last_status = "Reconnecting"
            state.phase = "Reconnecting"
            await asyncio.sleep(1)
            continue
        except Exception as exc:  # pragma: no cover - defensive programming
            state.last_error = str(exc)
            break
        finally:
            if ws is not None:
                await ws.close()
                store.add_log(
                    cp_path,
                    f"Closed (code={ws.close_code}, reason={getattr(ws, 'close_reason', '')})",
                    log_type="simulator",
                )

        if reset_event and reset_event.is_set():
            continue

        loop_count += 1
        if session_count == float("inf"):
            continue

    state.last_status = "Stopped"
    state.running = False
    state.phase = "Stopped"
    state.stop_time = time.strftime("%Y-%m-%d %H:%M:%S")
    _save_state_file(_simulators)


def simulate(
    *,
    host: str = "127.0.0.1",
    ws_port: Optional[int] = 8000,
    rfid: str = "FFFFFFFF",
    cp_path: str = "CPX",
    vin: str = "",
    serial_number: str = "",
    connector_id: int = 1,
    duration: int = 600,
    kw_min: float = 30.0,
    kw_max: float = 60.0,
    pre_charge_delay: float = 0.0,
    repeat: object = False,
    threads: Optional[int] = None,
    daemon: bool = True,
    interval: float = 5.0,
    username: Optional[str] = None,
    password: Optional[str] = None,
    cp: int = 1,
):
    """Entry point used by the admin interface.

    When ``daemon`` is ``True`` a coroutine is returned which must be awaited
    by the caller.  When ``daemon`` is ``False`` the function blocks until all
    sessions have completed.
    """

    session_count = parse_repeat(repeat)
    n_threads = int(threads) if threads else 1

    state = _simulators.get(cp, _simulators[1])
    state.last_command = "start"
    state.last_status = "Simulator launching..."
    state.running = True
    state.params = {
        "host": host,
        "ws_port": ws_port,
        "rfid": rfid,
        "cp_path": cp_path,
        "vin": vin,
        "serial_number": serial_number,
        "connector_id": connector_id,
        "duration": duration,
        "kw_min": kw_min,
        "kw_max": kw_max,
        "pre_charge_delay": pre_charge_delay,
        "repeat": repeat,
        "threads": threads,
        "daemon": daemon,
        "interval": interval,
        "username": username,
        "password": password,
    }
    state.start_time = time.strftime("%Y-%m-%d %H:%M:%S")
    state.stop_time = None
    _save_state_file(_simulators)

    async def orchestrate_all():
        tasks = []
        threads_list = []

        async def run_task(idx: int) -> None:
            this_cp_path = _unique_cp_path(cp_path, idx, n_threads)
            await simulate_cp(
                idx,
                host,
                ws_port,
                rfid,
                vin,
                this_cp_path,
                serial_number,
                connector_id,
                duration,
                kw_min,
                kw_max,
                pre_charge_delay,
                session_count,
                interval,
                username,
                password,
                sim_state=state,
            )

        def run_thread(idx: int) -> None:
            this_cp_path = _unique_cp_path(cp_path, idx, n_threads)
            asyncio.run(
                simulate_cp(
                    idx,
                    host,
                    ws_port,
                    rfid,
                    vin,
                    this_cp_path,
                    serial_number,
                    connector_id,
                    duration,
                    kw_min,
                    kw_max,
                    pre_charge_delay,
                    session_count,
                    interval,
                    username,
                    password,
                    sim_state=state,
                )
            )

        if n_threads == 1:
            tasks.append(asyncio.create_task(run_task(0)))
            try:
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:  # pragma: no cover - orchestration
                for t in tasks:
                    t.cancel()
                raise
        else:
            for idx in range(n_threads):
                t = threading.Thread(target=run_thread, args=(idx,), daemon=True)
                t.start()
                threads_list.append(t)
            try:
                while any(t.is_alive() for t in threads_list):
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:  # pragma: no cover
                pass
            finally:
                for t in threads_list:
                    t.join()

        state.last_status = "Simulator finished."
        state.running = False
        state.stop_time = time.strftime("%Y-%m-%d %H:%M:%S")
        _save_state_file(_simulators)

    if daemon:
        return orchestrate_all()

    if n_threads == 1:
        asyncio.run(
            simulate_cp(
                0,
                host,
                ws_port,
                rfid,
                vin,
                cp_path,
                serial_number,
                connector_id,
                duration,
                kw_min,
                kw_max,
                pre_charge_delay,
                session_count,
                interval,
                username,
                password,
                sim_state=state,
            )
        )
    else:
        threads_list = []
        for idx in range(n_threads):
            this_cp_path = _unique_cp_path(cp_path, idx, n_threads)
            t = threading.Thread(
                target=_thread_runner,
                args=(
                    simulate_cp,
                    idx,
                    host,
                    ws_port,
                    rfid,
                    vin,
                    this_cp_path,
                    serial_number,
                    connector_id,
                    duration,
                    kw_min,
                    kw_max,
                    pre_charge_delay,
                    session_count,
                    interval,
                    username,
                    password,
                ),
                kwargs={"sim_state": state},
                daemon=True,
            )
            t.start()
            threads_list.append(t)
        for t in threads_list:
            t.join()

    state.last_status = "Simulator finished."
    state.running = False
    state.stop_time = time.strftime("%Y-%m-%d %H:%M:%S")
    _save_state_file(_simulators)


# ---------------------------------------------------------------------------
# Convenience helpers used by administrative tasks
# ---------------------------------------------------------------------------


def _start_simulator(
    params: Optional[Dict[str, object]] = None, cp: int = 1
) -> tuple[bool, str, str]:
    """Start the simulator using the provided parameters.

    Returns a tuple ``(started, status_message, log_file)`` where ``started``
    indicates whether the simulator was launched successfully, the
    ``status_message`` reflects the result of attempting to connect and
    ``log_file`` is the path to the log capturing all simulator traffic.
    """

    state = _simulators[cp]
    cp_path = (params or {}).get(
        "cp_path", (state.params or {}).get("cp_path", f"CP{cp}")
    )
    log_file = str(store._file_path(cp_path, log_type="simulator"))

    if state.running:
        return False, "already running", log_file

    state.last_error = ""
    state.last_command = "start"
    state.last_status = "Simulator launching..."
    state.last_message = ""
    state.phase = "Starting"
    state.params = params or {}
    state.running = True
    state.start_time = time.strftime("%Y-%m-%d %H:%M:%S")
    state.stop_time = None
    _save_state_file(_simulators)

    coro = simulate(cp=cp, **state.params)
    threading.Thread(target=lambda: asyncio.run(coro), daemon=True).start()

    # Wait for initial connection result
    start_wait = time.time()
    status_msg = "Connection timeout"
    while time.time() - start_wait < 15:
        if state.last_error:
            state.running = False
            status_msg = f"Connection failed: {state.last_error}"
            break
        if state.phase == "Available":
            status_msg = "Connection accepted"
            break
        if not state.running:
            status_msg = "Connection failed"
            break
        time.sleep(0.1)

    state.last_status = status_msg
    _save_state_file(_simulators)

    return state.running and status_msg == "Connection accepted", status_msg, log_file


def _stop_simulator(cp: int = 1) -> bool:
    """Mark the simulator as requested to stop."""

    state = _simulators[cp]
    state.last_command = "stop"
    state.last_status = "Requested stop (will finish current run)..."
    state.phase = "Stopping"
    state.running = False
    _save_state_file(_simulators)
    return True


def _export_state(state: SimulatorState) -> Dict[str, object]:
    return {
        "running": state.running,
        "last_status": state.last_status,
        "last_command": state.last_command,
        "last_error": state.last_error,
        "last_message": state.last_message,
        "phase": state.phase,
        "start_time": state.start_time,
        "stop_time": state.stop_time,
        "params": state.params or {},
    }


def _simulator_status_json(cp: Optional[int] = None) -> str:
    """Return a JSON representation of the simulator state."""

    if cp is not None:
        return json.dumps(_export_state(_simulators[cp]), indent=2)
    return json.dumps(
        {str(idx): _export_state(st) for idx, st in _simulators.items()}, indent=2
    )


def get_simulator_state(cp: Optional[int] = None, refresh_file: bool = False):
    """Return the current simulator state.

    When ``refresh_file`` is ``True`` the persisted state file is reloaded.
    This mirrors the behaviour of the original implementation which allowed a
    separate process to query the running simulator.
    """

    if refresh_file:
        file_state = _load_state_file()
        for key, val in file_state.items():
            try:
                idx = int(key)
            except ValueError:  # pragma: no cover - defensive
                continue
            if idx in _simulators:
                _simulators[idx].__dict__.update(val)

    if cp is not None:
        return _export_state(_simulators[cp])
    return {idx: _export_state(st) for idx, st in _simulators.items()}


# The original file exposed ``view_cp_simulator`` which rendered an HTML user
# interface.  Implementing that functionality would require additional
# third‑party dependencies.  For the scope of the exercises the function is
# retained as a simple placeholder so importing the module does not fail.


def view_cp_simulator(*args, **kwargs):  # pragma: no cover - UI stub
    """Placeholder for the web based simulator view.

    The real project renders a dynamic HTML page.  Returning a short explanatory
    string keeps the public API compatible for callers that expect a return
    value while avoiding heavy dependencies.
    """

    return "Simulator web UI is not available in this environment."


def view_simulator(*args, **kwargs):  # pragma: no cover - simple alias
    return view_cp_simulator(*args, **kwargs)


__all__ = [
    "simulate",
    "simulate_cp",
    "parse_repeat",
    "_start_simulator",
    "_stop_simulator",
    "_simulator_status_json",
    "get_simulator_state",
    "view_cp_simulator",
    "view_simulator",
]

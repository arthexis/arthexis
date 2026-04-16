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
the simulators package.  The state tracking is intentionally simple but mirrors
the behaviour of the original code which recorded the last command executed and
whether the simulator was currently running.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from apps.ocpp import store
from apps.ocpp.cpsim_service import (
    CPSIM_START_QUEUED_STATUS,
    CPSIM_STOP_QUEUED_STATUS,
    cpsim_service_enabled,
    queue_cpsim_request,
)
from apps.simulators.runtime import ChargePointRuntime, ChargePointRuntimeConfig
from apps.simulators.evcs_mobilityhouse import MobilityHouseChargePointAdapter
from apps.simulators.network import validate_simulator_endpoint
from apps.simulators.simulator_runtime import (
    build_legacy_simulator_config,
    build_mobility_house_simulator_config,
    resolve_simulator_backend,
)

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


def _sanitize_params(params: Dict[str, object]) -> Dict[str, object]:
    sanitized = dict(params)
    for key in ("username", "password"):
        sanitized.pop(key, None)
    return sanitized


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

_runtime_adapters: Dict[int, object] = {}

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
                "params": _sanitize_params(v.params or {}),
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

def _normalize_payload(value: Any) -> dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {}


def _backend_simulator_from_payload(
    payload: Mapping[str, object],
    *,
    cp_idx: int = 1,
    sim_state: SimulatorState | None = None,
):
    preferred_backend = payload.get("simulator_backend")
    selection = resolve_simulator_backend(
        cp_idx=cp_idx,
        preferred_backend=str(preferred_backend) if preferred_backend is not None else None,
    )
    if selection.use_mobility_house:
        config = build_mobility_house_simulator_config(payload, cp_idx=cp_idx)
        return (
            selection,
            MobilityHouseChargePointAdapter(config, sim_state=sim_state),
        )
    config = build_legacy_simulator_config(payload, cp_idx=cp_idx)
    from apps.simulators.charge_point import ChargePointSimulator

    return (selection, ChargePointSimulator(config))

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
    average_kwh: float,
    amperage: float,
    pre_charge_delay: float,
    session_count: float,
    interval: float = 5.0,
    start_delay: float = 0.0,
    meter_interval: float | None = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    ws_scheme: Optional[str] = None,
    use_tls: Optional[bool] = None,
    allow_private_network: bool = False,
    *,
    sim_state: SimulatorState | None = None,
) -> None:
    """Simulate one charge point session."""

    validate_simulator_endpoint(
        host,
        ws_port,
        allow_private_network=allow_private_network,
    )

    state = sim_state or _simulators.get(cp_idx + 1, _simulators[1])
    config = ChargePointRuntimeConfig(
        cp_idx=cp_idx,
        host=host,
        ws_port=ws_port,
        rfid=rfid,
        vin=vin,
        cp_path=cp_path,
        serial_number=serial_number,
        connector_id=connector_id,
        duration=duration,
        average_kwh=average_kwh,
        amperage=amperage,
        pre_charge_delay=pre_charge_delay,
        session_count=session_count,
        interval=interval,
        start_delay=start_delay,
        meter_interval=meter_interval,
        username=username,
        password=password,
        ws_scheme=ws_scheme,
        use_tls=use_tls,
    )
    runtime = ChargePointRuntime(
        config,
        sim_state=state,
        log=lambda message: store.add_log(cp_path, message, log_type="simulator"),
        save_state=lambda: _save_state_file(_simulators),
    )
    await runtime.run()


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
    average_kwh: float = 60.0,
    amperage: float = 90.0,
    pre_charge_delay: float = 0.0,
    repeat: object = False,
    threads: Optional[int] = None,
    daemon: bool = True,
    interval: float = 5.0,
    username: Optional[str] = None,
    password: Optional[str] = None,
    allow_private_network: bool = False,
    ws_scheme: Optional[str] = None,
    use_tls: Optional[bool] = None,
    cp: int = 1,
    name: str = "Simulator",
    delay: Optional[float] = None,
    start_delay: float = 0.0,
    reconnect_slots: Optional[str] = None,
    demo_mode: bool = False,
    meter_interval: Optional[float] = None,
    **_: object,
):
    """Entry point used by the admin interface.

    When ``daemon`` is ``True`` a coroutine is returned which must be awaited
    by the caller.  When ``daemon`` is ``False`` the function blocks until all
    sessions have completed.
    """

    session_count = parse_repeat(repeat)
    n_threads = int(threads) if threads else 1

    validate_simulator_endpoint(
        host,
        ws_port,
        allow_private_network=allow_private_network,
    )

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
        "average_kwh": average_kwh,
        "amperage": amperage,
        "pre_charge_delay": pre_charge_delay,
        "repeat": repeat,
        "threads": threads,
        "daemon": daemon,
        "interval": interval,
        "username": username,
        "password": password,
        "allow_private_network": allow_private_network,
        "ws_scheme": ws_scheme,
        "use_tls": use_tls,
        "name": name,
        "delay": delay,
        "reconnect_slots": reconnect_slots,
        "demo_mode": demo_mode,
        "meter_interval": meter_interval,
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
                average_kwh,
                amperage,
                pre_charge_delay,
                session_count,
                interval,
                    start_delay,
                    meter_interval,
                    username,
                password,
                allow_private_network,
                ws_scheme,
                use_tls,
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
                    average_kwh,
                    amperage,
                    pre_charge_delay,
                    session_count,
                interval,
                    start_delay,
                    meter_interval,
                    username,
                    password,
                    allow_private_network,
                    ws_scheme,
                    use_tls,
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
                average_kwh,
                amperage,
                pre_charge_delay,
                session_count,
                interval,
                    start_delay,
                    meter_interval,
                    username,
                password,
                allow_private_network,
                ws_scheme,
                use_tls,
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
                    average_kwh,
                    amperage,
                    pre_charge_delay,
                    session_count,
                interval,
                    start_delay,
                    meter_interval,
                    username,
                    password,
                    allow_private_network,
                    ws_scheme,
                    use_tls,
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

    state = _simulators.setdefault(cp, SimulatorState())
    params = params or {}

    simulate_signature = inspect.signature(simulate)
    allowed_params = {
        name
        for name, param in simulate_signature.parameters.items()
        if param.kind != inspect.Parameter.VAR_KEYWORD and name != "cp"
    }
    filtered_params = {key: value for key, value in params.items() if key in allowed_params}
    preferred_backend = params.get("simulator_backend")
    if preferred_backend is not None:
        filtered_params["simulator_backend"] = str(preferred_backend)

    cp_path = filtered_params.get(
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
    state.params = filtered_params
    state.params.setdefault("start_delay", state.params.get("start_delay") if isinstance(state.params, dict) else None)
    state.params.pop("daemon", None)
    state.params.setdefault("allow_private_network", False)
    state.params.setdefault("start_delay", 0.0)
    state.running = True
    state.start_time = time.strftime("%Y-%m-%d %H:%M:%S")
    state.stop_time = None
    _save_state_file(_simulators)

    if cpsim_service_enabled():
        queue_cpsim_request(
            action="start",
            params=state.params,
            slot=cp,
            name=str(state.params.get("name") or f"Simulator {cp}"),
            source="landing",
        )
        state.last_status = CPSIM_START_QUEUED_STATUS
        state.phase = "Service"
        _save_state_file(_simulators)
        return True, state.last_status, log_file

    preferred_backend = state.params.get("simulator_backend") if isinstance(state.params, dict) else None
    selection = resolve_simulator_backend(
        cp_idx=cp,
        preferred_backend=str(preferred_backend) if preferred_backend is not None else None,
    )
    if selection.use_mobility_house:
        runtime_params = _normalize_payload(state.params)
        _, runtime = _backend_simulator_from_payload(
            runtime_params,
            cp_idx=cp,
            sim_state=state,
        )
        _runtime_adapters[cp] = runtime
        started, status, log_file = runtime.start()
        if not started:
            state.running = False
            state.last_error = status
            state.phase = "Error"
        state.last_status = status
        _save_state_file(_simulators)
        return started, status, log_file

    fallback_reason = ""
    if selection.feature_enabled and not selection.dependency_available:
        fallback_reason = selection.reason

    try:
        runtime_params = dict(state.params)
        runtime_params.pop("simulator_backend", None)
        coro = simulate(cp=cp, **runtime_params)
    except ValueError as exc:
        state.last_error = str(exc)
        state.last_status = "Invalid simulator configuration"
        state.phase = "Error"
        state.running = False
        _save_state_file(_simulators)
        return False, state.last_status, log_file

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

    if fallback_reason and status_msg.startswith("Connection"):
        status_msg = f"{fallback_reason} {status_msg}"

    state.last_status = status_msg
    _save_state_file(_simulators)

    accepted = status_msg.endswith("Connection accepted")
    return state.running and accepted, status_msg, log_file
def _stop_simulator(cp: int = 1) -> bool:
    """Mark the simulator as requested to stop."""

    state = _simulators[cp]
    state.last_command = "stop"
    state.last_status = "Requested stop (will finish current run)..."
    state.phase = "Stopping"
    state.running = False
    _save_state_file(_simulators)
    if cpsim_service_enabled():
        queue_cpsim_request(
            action="stop",
            slot=cp,
            name=str((state.params or {}).get("name") or f"Simulator {cp}"),
            source="landing",
            params=state.params,
        )
        state.last_status = CPSIM_STOP_QUEUED_STATUS
        state.phase = "Service"
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
        "params": _sanitize_params(state.params or {}),
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




























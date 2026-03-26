import asyncio
import json
from types import SimpleNamespace

import pytest
import websockets

from apps.simulators.runtime import ChargePointRuntime, ChargePointRuntimeConfig


class FakeWebSocket:
    def __init__(self, responses=None, *, subprotocol="ocpp1.6j"):
        self.responses = list(responses or [])
        self.subprotocol = subprotocol
        self.close_code = 1000
        self.close_reason = ""
        self.sent_messages: list[str] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent_messages.append(message)

    async def recv(self) -> str:
        if not self.responses:
            raise asyncio.CancelledError()
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True


def _runtime_config(**kwargs) -> ChargePointRuntimeConfig:
    values = {
        "cp_idx": 1,
        "host": "127.0.0.1",
        "ws_port": 9000,
        "rfid": "FFFFFFFF",
        "vin": "VIN-1",
        "cp_path": "CP-1",
        "serial_number": "SERIAL-1",
        "connector_id": 1,
        "duration": 0,
        "average_kwh": 1.0,
        "amperage": 16.0,
        "pre_charge_delay": 0,
        "session_count": 1,
        "interval": 0.01,
        "start_delay": 0.0,
        "meter_interval": 0.01,
        "username": None,
        "password": None,
        "ws_scheme": "ws",
        "use_tls": None,
    }
    values.update(kwargs)
    return ChargePointRuntimeConfig(**values)


def _runtime(config: ChargePointRuntimeConfig, *, connect):
    state = SimpleNamespace(
        running=True,
        phase="",
        last_message="",
        last_error="",
        last_status="",
        stop_time=None,
    )
    return ChargePointRuntime(
        config,
        sim_state=state,
        log=lambda _message: None,
        save_state=lambda: None,
        connect=connect,
    )


@pytest.mark.anyio
async def test_runtime_establish_connection_falls_back_to_alternate_scheme():
    attempts: list[tuple[str, tuple[str, ...]]] = []
    ws = FakeWebSocket()

    async def connect(uri: str, **kwargs):
        attempts.append((uri, tuple(kwargs.get("subprotocols", []))))
        if uri.startswith("ws://"):
            raise RuntimeError("ws unavailable")
        return ws

    runtime = _runtime(_runtime_config(ws_scheme="ws"), connect=connect)

    connected = await runtime.establish_connection()

    assert connected is ws
    attempted_schemes = {uri.split("://", 1)[0] for uri, _ in attempts}
    assert attempted_schemes == {"ws", "wss"}


@pytest.mark.anyio
async def test_runtime_establish_connection_does_not_downgrade_explicit_wss():
    attempts: list[str] = []
    ws = FakeWebSocket()

    async def connect(uri: str, **kwargs):
        attempts.append(uri)
        return ws

    runtime = _runtime(_runtime_config(ws_scheme="wss"), connect=connect)

    connected = await runtime.establish_connection()

    assert connected is ws
    assert all(uri.startswith("wss://") for uri in attempts)


@pytest.mark.anyio
async def test_runtime_command_listener_handles_remote_stop_and_reset():
    ws = FakeWebSocket(
        responses=[
            json.dumps([2, "stop-1", "RemoteStopTransaction", {}]),
            json.dumps([2, "reset-1", "Reset", {}]),
        ]
    )
    runtime = _runtime(_runtime_config(), connect=lambda *args, **kwargs: ws)

    stop_event, reset_event, listener = runtime.setup_command_listener(ws)
    await asyncio.wait_for(stop_event.wait(), timeout=1)
    await asyncio.wait_for(reset_event.wait(), timeout=1)

    listener.cancel()
    with pytest.raises(asyncio.CancelledError):
        await listener

    replies = [json.loads(frame) for frame in ws.sent_messages]
    assert [frame[:2] for frame in replies] == [[3, "stop-1"], [3, "reset-1"]]


@pytest.mark.anyio
async def test_runtime_run_reconnects_after_connection_closed_error():
    config = _runtime_config(session_count=1)
    runtime = _runtime(config, connect=lambda *args, **kwargs: FakeWebSocket())
    websocket_1 = FakeWebSocket()
    websocket_2 = FakeWebSocket()
    close_calls: list[FakeWebSocket] = []
    session_runs = {"count": 0}

    async def establish_connection():
        return websocket_1 if session_runs["count"] == 0 else websocket_2

    async def perform_ocpp_handshake(_ws):
        return None

    async def run_charging_session_loop(_ws):
        session_runs["count"] += 1
        if session_runs["count"] == 1:
            raise websockets.ConnectionClosedError(None, None)
        return False

    async def close(ws):
        close_calls.append(ws)

    runtime.establish_connection = establish_connection
    runtime.perform_ocpp_handshake = perform_ocpp_handshake
    runtime.run_charging_session_loop = run_charging_session_loop
    runtime.close = close

    await runtime.run()

    assert session_runs["count"] == 2
    assert close_calls == [websocket_1, websocket_2]
    assert runtime.state.last_status == "Stopped"
    assert runtime.state.phase == "Stopped"

import json

import pytest
from asgiref.sync import async_to_sync

from apps.simulators.charge_point import ChargePointSimulator, SimulatorConfig


class FakeWebSocket:
    def __init__(self, responses=None, *, subprotocol="ocpp1.6j", close_code=1000):
        self.responses = list(responses or [])
        self.subprotocol = subprotocol
        self.close_code = close_code
        self.close_reason = ""
        self.sent_messages = []
        self.closed = False

    async def send(self, message):
        self.sent_messages.append(message)

    async def recv(self):
        if not self.responses:
            raise AssertionError("No websocket response queued")
        next_item = self.responses.pop(0)
        if isinstance(next_item, BaseException):
            raise next_item
        return next_item

    async def close(self):
        self.closed = True


@pytest.fixture
def simulator(monkeypatch):
    monkeypatch.setattr(
        "apps.simulators.charge_point.validate_simulator_endpoint",
        lambda *args, **kwargs: None,
    )
    return ChargePointSimulator(
        SimulatorConfig(
            host="127.0.0.1",
            ws_port=9000,
            cp_path="CP-TEST/",
            pre_charge_delay=0,
            duration=0,
            interval=0.01,
        )
    )


def test_run_session_falls_back_to_alternate_scheme(simulator, monkeypatch):
    attempts = []
    websocket = FakeWebSocket(
        responses=[
            json.dumps([3, "boot", {"status": "Accepted"}]),
            json.dumps([3, "auth", {"idTagInfo": {"status": "Accepted"}}]),
        ]
    )

    async def fake_connect(uri, **kwargs):
        attempts.append((uri, tuple(kwargs.get("subprotocols", []))))
        if uri.startswith("ws://"):
            raise RuntimeError(f"cannot connect to {uri}")
        return websocket

    monkeypatch.setattr("apps.simulators.charge_point.websockets.connect", fake_connect)

    async_to_sync(simulator._run_session)()

    assert [uri for uri, _subprotocols in attempts[:3]] == [
        "ws://127.0.0.1:9000/CP-TEST/",
        "ws://127.0.0.1:9000/CP-TEST/",
        "ws://127.0.0.1:9000/CP-TEST/",
    ]
    assert attempts[3] == ("wss://127.0.0.1:9000/CP-TEST/", ("ocpp1.6j",))
    assert simulator._connect_error == "accepted"
    assert simulator._last_ws_subprotocol == "ocpp1.6j"
    assert websocket.closed is True


def test_run_session_marks_boot_rejection(simulator, monkeypatch):
    websocket = FakeWebSocket(
        responses=[json.dumps([3, "boot", {"status": "Rejected"}])]
    )

    async def fake_connect(*args, **kwargs):
        return websocket

    monkeypatch.setattr("apps.simulators.charge_point.websockets.connect", fake_connect)

    async_to_sync(simulator._run_session)()

    assert simulator._connected.is_set() is True
    assert simulator._connect_error == "Boot status Rejected"
    assert simulator.status == "stopped"
    assert websocket.closed is True


def test_run_session_stops_on_receive_timeout(simulator, monkeypatch):
    websocket = FakeWebSocket(
        responses=[
            json.dumps([3, "boot", {"status": "Accepted"}]),
            TimeoutError(),
        ]
    )

    async def fake_connect(*args, **kwargs):
        return websocket

    monkeypatch.setattr("apps.simulators.charge_point.websockets.connect", fake_connect)

    async_to_sync(simulator._run_session)()

    assert simulator._connect_error == "Timeout waiting for response"
    assert simulator.status == "stopped"
    assert simulator._stop_event.is_set() is True
    assert websocket.closed is True


def test_run_session_honors_early_stop_request(simulator, monkeypatch):
    websocket = FakeWebSocket(
        responses=[
            json.dumps([3, "boot", {"status": "Accepted"}]),
            json.dumps([3, "auth", {"idTagInfo": {"status": "Accepted"}}]),
        ]
    )
    started = {"value": False}

    async def fake_connect(*args, **kwargs):
        return websocket

    async def fake_handshake():
        accepted = await ChargePointSimulator._perform_boot_and_authorize_handshake(simulator)
        simulator._stop_event.set()
        return accepted

    async def fail_if_started():
        started["value"] = True
        raise AssertionError("start transaction should not run after stop request")

    monkeypatch.setattr("apps.simulators.charge_point.websockets.connect", fake_connect)
    monkeypatch.setattr(simulator, "_perform_boot_and_authorize_handshake", fake_handshake)
    monkeypatch.setattr(simulator, "_start_transaction", fail_if_started)
    simulator.config.pre_charge_delay = 5

    async_to_sync(simulator._run_session)()

    assert started["value"] is False
    assert simulator.status == "stopped"
    assert simulator._connect_error == "accepted"
    assert websocket.closed is True

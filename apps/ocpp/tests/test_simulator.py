import asyncio
import json
import os
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from django.core.management import CommandError, call_command
from websockets.exceptions import WebSocketException

from apps.ocpp.management.commands.ocpp import Command
from apps.ocpp.simulator.client import OCPP16Simulator, SimulatorConfig, SimulatorError
from apps.ocpp.simulator.scenarios import AuthorizeScenario
from apps.ocpp.simulator.worker import (
    DEFAULT_MAX_INSTANCES,
    SimulatorWorker,
    _secure_socket_path,
    error_path,
    runtime_dir,
    session_path,
    socket_path,
)


def insecure_config(**kwargs):
    return SimulatorConfig(allow_insecure_ws=True, **kwargs)


def test_simulator_endpoint_uses_ocpp_route_and_charger_identity():
    config = insecure_config(url="ws://example.test:9000/", charger="CP 01")
    assert config.endpoint == "ws://example.test:9000/ocpp/CP%2001"


def test_insecure_ws_requires_explicit_opt_in():
    config = SimulatorConfig(url="ws://example.test:9000", charger="CP01")
    with pytest.raises(SimulatorError, match="allow-insecure-ws"):
        _ = config.endpoint


def test_wss_is_allowed_by_default():
    config = SimulatorConfig(url="wss://example.test", charger="CP01")
    assert config.endpoint == "wss://example.test/ocpp/CP01"


def test_authorize_scenario_reuses_already_booted_simulator():
    simulator = SimpleNamespace(authorize=AsyncMock(return_value="Accepted"))
    result = asyncio.run(
        AuthorizeScenario("GWAY001", "TEST001", "Accepted").run(simulator)
    )
    simulator.authorize.assert_awaited_once_with("TEST001")
    assert result.to_dict() == {
        "charger": "GWAY001",
        "boot": "Accepted",
        "id_tag": "TEST001",
        "authorization": "Accepted",
    }


def test_worker_authorize_uses_same_simulator_connection():
    worker = SimulatorWorker(
        insecure_config(url="ws://example.test", charger="GWAY001")
    )
    worker._boot = SimpleNamespace(status="Accepted", interval=60)
    worker._simulator.authorize = AsyncMock(return_value="Invalid")
    response = asyncio.run(
        worker._dispatch({"action": "authorize", "id_tag": "UNKNOWN001"})
    )
    assert response["authorization"] == "Invalid"
    worker._simulator.authorize.assert_awaited_once_with("UNKNOWN001")


def test_worker_close_marks_worker_for_shutdown():
    worker = SimulatorWorker(
        insecure_config(url="ws://example.test", charger="GWAY001")
    )
    response = asyncio.run(worker._dispatch({"action": "close"}))
    assert response["closed"] is True
    assert worker._stop.is_set()


def test_heartbeat_does_not_reset_control_activity():
    async def exercise():
        worker = SimulatorWorker(
            insecure_config(url="ws://example.test", charger="GWAY001")
        )
        worker._boot = SimpleNamespace(status="Accepted", interval=0.001)
        worker._simulator.call = AsyncMock(return_value={"currentTime": "now"})
        before = worker._last_control_activity
        task = asyncio.create_task(worker._heartbeat_loop())
        await asyncio.sleep(0.01)
        worker._stop.set()
        await task
        return worker, before

    worker, before = asyncio.run(exercise())
    assert worker._simulator.call.await_count >= 1
    assert worker._last_control_activity == before


def test_unsupported_csms_call_returns_call_error():
    async def exercise():
        simulator = OCPP16Simulator(
            insecure_config(url="ws://example.test", charger="GWAY001")
        )
        simulator._connection = SimpleNamespace(send=AsyncMock())
        envelope = SimpleNamespace(
            message_id="server-1", action="RemoteStartTransaction", payload={}
        )
        await simulator._handle_csms_call(envelope)
        return simulator

    simulator = asyncio.run(exercise())
    message = json.loads(simulator._connection.send.await_args.args[0])
    assert message[:3] == [4, "server-1", "NotSupported"]
    assert "RemoteStartTransaction" in message[3]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"status": "Accepted", "interval": 60},
        {"status": "Accepted", "currentTime": "now"},
        {"status": "Accepted", "currentTime": "now", "interval": "60"},
        {"status": "Accepted", "currentTime": "now", "interval": True},
    ],
)
def test_boot_rejects_incomplete_or_malformed_response(payload):
    async def exercise():
        simulator = OCPP16Simulator(
            insecure_config(url="ws://example.test", charger="GWAY001")
        )
        simulator.call = AsyncMock(return_value=payload)
        with pytest.raises(SimulatorError, match="BootNotification response"):
            await simulator.boot()

    asyncio.run(exercise())


def test_connect_normalizes_websocket_failures(monkeypatch):
    class BrokenWebSocket(WebSocketException):
        pass

    async def broken_connect(*args, **kwargs):
        raise BrokenWebSocket("handshake failed")

    monkeypatch.setattr("apps.ocpp.simulator.client.connect", broken_connect)

    async def exercise():
        simulator = OCPP16Simulator(
            insecure_config(url="ws://example.test", charger="GWAY001")
        )
        with pytest.raises(SimulatorError, match="failed to connect"):
            await simulator.connect()
        assert simulator._connection is None

    asyncio.run(exercise())


def test_receive_failure_invalidates_connection_and_allows_reconnect(monkeypatch):
    class BrokenConnection:
        subprotocol = "ocpp1.6"

        def __init__(self):
            self.closed = False

        async def recv(self):
            raise WebSocketException("connection lost")

        async def close(self):
            self.closed = True

    class HealthyConnection:
        subprotocol = "ocpp1.6"

        def __init__(self):
            self.closed = False
            self.block = asyncio.Event()

        async def recv(self):
            await self.block.wait()

        async def close(self):
            self.closed = True

    async def exercise():
        simulator = OCPP16Simulator(
            insecure_config(url="ws://example.test", charger="GWAY001")
        )
        broken = BrokenConnection()
        simulator._connection = broken
        task = asyncio.create_task(simulator._receive_loop())
        simulator._receive_task = task
        await task

        assert simulator._connection is None
        assert simulator._receive_task is None
        assert broken.closed is True

        healthy = HealthyConnection()

        async def reconnect(*args, **kwargs):
            return healthy

        monkeypatch.setattr("apps.ocpp.simulator.client.connect", reconnect)
        await simulator.connect()
        assert simulator._connection is healthy
        assert simulator._receive_task is not None
        await simulator.close()
        assert healthy.closed is True

    asyncio.run(exercise())


def test_matching_call_error_is_correlated_to_pending_call():
    class ErrorConnection:
        subprotocol = "ocpp1.6"

        def __init__(self):
            self.queue = asyncio.Queue()

        async def send(self, raw):
            message = json.loads(raw)
            await self.queue.put(
                json.dumps([4, message[1], "SecurityError", "denied", {}])
            )

        async def recv(self):
            return await self.queue.get()

        async def close(self):
            return None

    async def exercise():
        simulator = OCPP16Simulator(
            insecure_config(url="ws://example.test", charger="GWAY001")
        )
        simulator._connection = ErrorConnection()
        simulator._receive_task = asyncio.create_task(simulator._receive_loop())
        try:
            with pytest.raises(SimulatorError, match="SecurityError"):
                await simulator.call("Authorize", {"idTag": "TEST"})
        finally:
            await simulator.close()

    asyncio.run(exercise())


def test_call_timeout_is_not_extended_by_unmatched_frames():
    class NoisyConnection:
        subprotocol = "ocpp1.6"

        async def send(self, raw):
            return None

        async def recv(self):
            await asyncio.sleep(0.003)
            return json.dumps([3, "unrelated-message", {}])

        async def close(self):
            return None

    async def exercise():
        simulator = OCPP16Simulator(
            insecure_config(
                url="ws://example.test", charger="GWAY001", timeout=0.03
            )
        )
        simulator._connection = NoisyConnection()
        simulator._receive_task = asyncio.create_task(simulator._receive_loop())
        started = asyncio.get_running_loop().time()
        try:
            with pytest.raises(SimulatorError, match="timed out"):
                await simulator.call("Authorize", {"idTag": "TEST"})
        finally:
            elapsed = asyncio.get_running_loop().time() - started
            await simulator.close()
        assert elapsed < 0.12

    asyncio.run(exercise())


def test_runtime_dir_and_control_socket_are_owner_only(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    root.mkdir(mode=0o777)
    os.chmod(root, 0o777)
    monkeypatch.setenv("OCPP_SIMULATOR_RUNTIME_DIR", str(root))

    secured_root = runtime_dir()
    assert stat.S_IMODE(secured_root.stat().st_mode) == 0o700

    sock = root / "test.sock"
    sock.touch()
    os.chmod(sock, 0o666)
    _secure_socket_path(sock)
    assert stat.S_IMODE(sock.stat().st_mode) == 0o600


def test_distinct_charger_ids_have_distinct_runtime_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("OCPP_SIMULATOR_RUNTIME_DIR", str(tmp_path))
    assert session_path("CP/A") != session_path("CP?A")
    assert socket_path("CP/A") != socket_path("CP?A")


def test_startup_failure_terminates_worker_and_cleans_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("OCPP_SIMULATOR_RUNTIME_DIR", str(tmp_path))
    charger = "GWAY001"
    for path in (session_path(charger), socket_path(charger), error_path(charger)):
        path.write_text("stale")

    process = Mock()
    process.poll.return_value = None
    Command._stop_starting_worker(process, charger)

    process.terminate.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=5)
    process.kill.assert_not_called()
    assert not session_path(charger).exists()
    assert not socket_path(charger).exists()
    assert not error_path(charger).exists()


def test_default_instance_limit_is_two(monkeypatch):
    monkeypatch.delenv("OCPP_SIMULATOR_MAX_INSTANCES", raising=False)
    from apps.ocpp.simulator.worker import max_instances

    assert max_instances() == DEFAULT_MAX_INSTANCES == 2


def test_instance_limit_can_be_overridden(monkeypatch):
    monkeypatch.setenv("OCPP_SIMULATOR_MAX_INSTANCES", "7")
    from apps.ocpp.simulator.worker import max_instances

    assert max_instances() == 7


def test_authorize_requires_open_simulator(tmp_path, monkeypatch):
    monkeypatch.setenv("OCPP_SIMULATOR_RUNTIME_DIR", str(tmp_path))
    with pytest.raises(CommandError, match="is not open"):
        call_command(
            "ocpp",
            "simulator",
            "authorize",
            "--charger",
            "MISSING",
            "--id-tag",
            "X",
        )


def test_open_rejects_plaintext_without_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("OCPP_SIMULATOR_RUNTIME_DIR", str(tmp_path))
    with pytest.raises(CommandError, match="allow-insecure-ws"):
        call_command(
            "ocpp",
            "simulator",
            "open",
            "--url",
            "ws://example.test:9000",
            "--charger",
            "GWAY001",
        )

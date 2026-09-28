import asyncio
import json
from collections.abc import Callable

import pytest

from apps.ocpp.simulator.network import (
    LiveOcpp16Simulator,
    LiveSimulatorConfig,
    LiveSimulatorError,
)


def insecure_config(**kwargs):
    return LiveSimulatorConfig(allow_insecure_ws=True, **kwargs)


class ScriptedConnection:
    subprotocol = "ocpp1.6"

    def __init__(self, responder: Callable[[list[object]], list[object]]) -> None:
        self._responder = responder
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self.closed = False

    async def send(self, raw: str) -> None:
        message = json.loads(raw)
        response = self._responder(message)
        await self._queue.put(json.dumps(response))

    async def recv(self) -> str:
        return await self._queue.get()

    async def close(self) -> None:
        self.closed = True


def connection_factory(*connections):
    pending = iter(connections)

    async def factory(*args, **kwargs):
        return next(pending)

    return factory


def test_endpoint_uses_current_ocpp_route_and_encoded_identity():
    config = insecure_config(url="ws://example.test:9000/", charger="CP 01")
    assert config.endpoint == "ws://example.test:9000/ocpp/CP%2001"


def test_plaintext_websocket_requires_explicit_opt_in():
    config = LiveSimulatorConfig(url="ws://example.test:9000", charger="CP01")
    with pytest.raises(LiveSimulatorError, match="allow-insecure-ws"):
        _ = config.endpoint


def test_boot_and_authorize_validate_real_csms_payloads():
    def respond(message):
        _, unique_id, action, _ = message
        if action == "BootNotification":
            return [
                3,
                unique_id,
                {
                    "status": "Accepted",
                    "currentTime": "2026-09-28T03:00:00Z",
                    "interval": 60,
                },
            ]
        if action == "Authorize":
            return [3, unique_id, {"idTagInfo": {"status": "Accepted"}}]
        raise AssertionError(action)

    async def exercise():
        connection = ScriptedConnection(respond)
        simulator = LiveOcpp16Simulator(
            insecure_config(url="ws://example.test", charger="GWAY001"),
            connection_factory=connection_factory(connection),
        )
        await simulator.connect()
        try:
            boot = await simulator.boot()
            status = await simulator.authorize("TEST001")
        finally:
            await simulator.close()

        assert boot.status == "Accepted"
        assert boot.interval == 60
        assert status == "Accepted"
        assert connection.closed is True

    asyncio.run(exercise())


def test_call_result_is_correlated_to_pending_call():
    def respond(message):
        return [3, message[1], {"ok": True}]

    async def exercise():
        connection = ScriptedConnection(respond)
        simulator = LiveOcpp16Simulator(
            insecure_config(url="ws://example.test", charger="GWAY001"),
            connection_factory=connection_factory(connection),
        )
        await simulator.connect()
        try:
            response = await simulator.call("Heartbeat", {})
        finally:
            await simulator.close()
        assert response == {"ok": True}

    asyncio.run(exercise())


def test_call_error_is_exposed_as_simulator_error():
    def respond(message):
        return [4, message[1], "SecurityError", "denied", {}]

    async def exercise():
        connection = ScriptedConnection(respond)
        simulator = LiveOcpp16Simulator(
            insecure_config(url="ws://example.test", charger="GWAY001"),
            connection_factory=connection_factory(connection),
        )
        await simulator.connect()
        try:
            with pytest.raises(LiveSimulatorError, match="SecurityError"):
                await simulator.call("Authorize", {"idTag": "TEST"})
        finally:
            await simulator.close()

    asyncio.run(exercise())


def test_reconnect_replaces_transport():
    def no_response(message):
        raise AssertionError("no OCPP call expected")

    first = ScriptedConnection(no_response)
    second = ScriptedConnection(no_response)

    async def exercise():
        simulator = LiveOcpp16Simulator(
            insecure_config(url="ws://example.test", charger="GWAY001"),
            connection_factory=connection_factory(first, second),
        )
        await simulator.connect()
        assert simulator.connected is True

        await simulator.reconnect()

        assert first.closed is True
        assert simulator.connected is True

        await simulator.close()
        assert second.closed is True
        assert simulator.connected is False

    asyncio.run(exercise())

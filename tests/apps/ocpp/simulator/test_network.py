import asyncio
import json

import pytest

from apps.ocpp.simulator.network import (
    LiveOcpp16Simulator,
    LiveSimulatorConfig,
    LiveSimulatorError,
)


def insecure_config(**kwargs):
    return LiveSimulatorConfig(allow_insecure_ws=True, **kwargs)


def test_endpoint_uses_current_ocpp_route_and_encoded_identity():
    config = insecure_config(url="ws://example.test:9000/", charger="CP 01")
    assert config.endpoint == "ws://example.test:9000/ocpp/CP%2001"


def test_plaintext_websocket_requires_explicit_opt_in():
    config = LiveSimulatorConfig(url="ws://example.test:9000", charger="CP01")
    with pytest.raises(LiveSimulatorError, match="allow-insecure-ws"):
        _ = config.endpoint


def test_boot_and_authorize_validate_real_csms_payloads():
    async def exercise():
        simulator = LiveOcpp16Simulator(
            insecure_config(url="ws://example.test", charger="GWAY001")
        )
        responses = iter(
            [
                {
                    "status": "Accepted",
                    "currentTime": "2026-09-28T03:00:00Z",
                    "interval": 60,
                },
                {"idTagInfo": {"status": "Accepted"}},
            ]
        )

        async def call(action, payload):
            return next(responses)

        simulator.call = call
        boot = await simulator.boot()
        status = await simulator.authorize("TEST001")
        assert boot.status == "Accepted"
        assert boot.interval == 60
        assert status == "Accepted"

    asyncio.run(exercise())


def test_call_result_is_correlated_to_pending_call():
    class Connection:
        subprotocol = "ocpp1.6"

        def __init__(self):
            self.queue = asyncio.Queue()
            self.closed = False

        async def send(self, raw):
            message = json.loads(raw)
            await self.queue.put(json.dumps([3, message[1], {"ok": True}]))

        async def recv(self):
            return await self.queue.get()

        async def close(self):
            self.closed = True

    async def exercise():
        simulator = LiveOcpp16Simulator(
            insecure_config(url="ws://example.test", charger="GWAY001")
        )
        connection = Connection()
        simulator._connection = connection
        simulator._receive_task = asyncio.create_task(
            simulator._receive_loop(connection)
        )
        try:
            response = await simulator.call("Heartbeat", {})
            assert response == {"ok": True}
        finally:
            await simulator.close()

    asyncio.run(exercise())


def test_call_error_is_exposed_as_simulator_error():
    class Connection:
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
        simulator = LiveOcpp16Simulator(
            insecure_config(url="ws://example.test", charger="GWAY001")
        )
        connection = Connection()
        simulator._connection = connection
        simulator._receive_task = asyncio.create_task(
            simulator._receive_loop(connection)
        )
        try:
            with pytest.raises(LiveSimulatorError, match="SecurityError"):
                await simulator.call("Authorize", {"idTag": "TEST"})
        finally:
            await simulator.close()

    asyncio.run(exercise())


def test_reconnect_replaces_failed_transport(monkeypatch):
    class Connection:
        subprotocol = "ocpp1.6"

        def __init__(self):
            self.closed = False
            self.block = asyncio.Event()

        async def recv(self):
            await self.block.wait()

        async def close(self):
            self.closed = True

    first = Connection()
    second = Connection()
    connections = iter([first, second])

    async def fake_connect(*args, **kwargs):
        return next(connections)

    monkeypatch.setattr("apps.ocpp.simulator.network.connect", fake_connect)

    async def exercise():
        simulator = LiveOcpp16Simulator(
            insecure_config(url="ws://example.test", charger="GWAY001")
        )
        await simulator.connect()
        assert simulator._connection is first
        await simulator.reconnect()
        assert first.closed is True
        assert simulator._connection is second
        await simulator.close()

    asyncio.run(exercise())

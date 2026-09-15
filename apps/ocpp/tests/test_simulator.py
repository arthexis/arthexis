import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from django.core.management import call_command

from apps.ocpp.simulator.client import OCPP16Simulator, SimulatorConfig
from apps.ocpp.simulator.scenarios import AuthorizeScenario
from apps.ocpp.simulator.worker import DEFAULT_MAX_INSTANCES, SimulatorWorker


def test_simulator_endpoint_uses_ocpp_route_and_charger_identity():
    config = SimulatorConfig(url="ws://example.test:9000/", charger="CP 01")
    assert config.endpoint == "ws://example.test:9000/ocpp/CP%2001"


def test_authorize_scenario_reuses_already_booted_simulator():
    simulator = SimpleNamespace(authorize=AsyncMock(return_value="Accepted"))
    result = asyncio.run(
        AuthorizeScenario("GWAY001", "TEST001", "Accepted").run(simulator)
    )
    simulator.authorize.assert_awaited_once_with("TEST001")
    assert result.as_dict() == {
        "charger": "GWAY001",
        "boot": "Accepted",
        "id_tag": "TEST001",
        "authorization": "Accepted",
    }


def test_worker_authorize_uses_same_simulator_connection():
    worker = SimulatorWorker(
        SimulatorConfig(url="ws://example.test", charger="GWAY001")
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
        SimulatorConfig(url="ws://example.test", charger="GWAY001")
    )
    response = asyncio.run(worker._dispatch({"action": "close"}))
    assert response["closed"] is True
    assert worker._stop.is_set()


def test_heartbeat_does_not_reset_control_activity():
    async def exercise():
        worker = SimulatorWorker(
            SimulatorConfig(url="ws://example.test", charger="GWAY001")
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
            SimulatorConfig(url="ws://example.test", charger="GWAY001")
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
    with pytest.raises(Exception, match="is not open"):
        call_command(
            "ocpp", "simulator", "authorize", "--charger", "MISSING", "--id-tag", "X"
        )

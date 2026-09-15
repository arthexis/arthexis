import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from django.core.management import call_command

from apps.ocpp.simulator.client import OCPP16Simulator, SimulatorConfig, SimulatorError
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
    worker._boot = SimpleNamespace(status="Accepted")
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


def test_default_instance_limit_is_two(monkeypatch):
    monkeypatch.delenv("OCPP_SIMULATOR_MAX_INSTANCES", raising=False)
    from apps.ocpp.simulator.worker import max_instances

    assert max_instances() == DEFAULT_MAX_INSTANCES == 2


def test_instance_limit_can_be_overridden(monkeypatch):
    monkeypatch.setenv("OCPP_SIMULATOR_MAX_INSTANCES", "7")
    from apps.ocpp.simulator.worker import max_instances

    assert max_instances() == 7


def test_simulator_client_sends_boot_then_authorize_on_one_connection():
    responses = iter(
        [
            [3, "boot-id", {"status": "Accepted", "currentTime": "2026-09-15T00:00:00Z", "interval": 60}],
            [3, "auth-id", {"idTagInfo": {"status": "Accepted"}}],
        ]
    )
    connection = SimpleNamespace(
        subprotocol="ocpp1.6",
        send=AsyncMock(),
        recv=AsyncMock(side_effect=lambda: json.dumps(next(responses))),
        close=AsyncMock(),
    )

    async def exercise():
        simulator = OCPP16Simulator(
            SimulatorConfig(url="ws://example.test:9000", charger="GWAY001")
        )
        simulator._connection = connection
        with patch("apps.ocpp.simulator.client.uuid.uuid4") as uuid4:
            uuid4.side_effect = [SimpleNamespace(hex="boot-id"), SimpleNamespace(hex="auth-id")]
            boot = await simulator.boot()
            status = await simulator.authorize("TEST001")
        return boot, status

    boot, status = asyncio.run(exercise())
    assert boot.status == "Accepted"
    assert status == "Accepted"
    sent = [json.loads(call.args[0]) for call in connection.send.await_args_list]
    assert [message[2] for message in sent] == ["BootNotification", "Authorize"]


def test_authorize_requires_open_simulator(tmp_path, monkeypatch):
    monkeypatch.setenv("OCPP_SIMULATOR_RUNTIME_DIR", str(tmp_path))
    with pytest.raises(Exception, match="is not open"):
        call_command(
            "ocpp", "simulator", "authorize", "--charger", "MISSING", "--id-tag", "X"
        )

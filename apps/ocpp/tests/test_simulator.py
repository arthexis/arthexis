import json

import pytest

from apps.ocpp.simulator import AuthorizeScenario, OCPP16Simulator, SimulatorConfig


class FakeConnection:
    subprotocol = "ocpp1.6"

    def __init__(self):
        self.sent = []
        self.responses = []
        self.closed = False

    async def send(self, raw):
        message = json.loads(raw)
        self.sent.append(message)
        _, message_id, action, payload = message
        if action == "BootNotification":
            assert payload["chargePointVendor"] == "Test Vendor"
            self.responses.append(
                json.dumps(
                    [
                        3,
                        message_id,
                        {
                            "status": "Accepted",
                            "currentTime": "2026-09-15T00:00:00Z",
                            "interval": 60,
                        },
                    ]
                )
            )
        elif action == "Authorize":
            assert payload == {"idTag": "UNKNOWN-001"}
            self.responses.append(
                json.dumps([3, message_id, {"idTagInfo": {"status": "Invalid"}}])
            )

    async def recv(self):
        return self.responses.pop(0)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_authorize_scenario_uses_one_connection_and_reports_remote_decision(
    monkeypatch,
):
    connection = FakeConnection()
    connect_calls = []

    async def fake_connect(url, **kwargs):
        connect_calls.append((url, kwargs))
        return connection

    monkeypatch.setattr("apps.ocpp.simulator.client.connect", fake_connect)
    config = SimulatorConfig(
        url="ws://satellite:9000",
        charger="GWAY 001",
        vendor="Test Vendor",
        model="Test Model",
    )

    result = await AuthorizeScenario("UNKNOWN-001").run(OCPP16Simulator(config))

    assert connect_calls == [
        (
            "ws://satellite:9000/ocpp/GWAY%20001",
            {"subprotocols": ["ocpp1.6"], "open_timeout": 30.0},
        )
    ]
    assert [message[2] for message in connection.sent] == [
        "BootNotification",
        "Authorize",
    ]
    assert connection.closed is True
    assert result.charger == "GWAY 001"
    assert result.id_tag == "UNKNOWN-001"
    assert result.boot.status == "Accepted"
    assert result.authorization == "Invalid"
    assert result.to_dict()["authorization"] == "Invalid"


def test_simulator_config_builds_ocpp_endpoint():
    config = SimulatorConfig(url="ws://host/base/", charger="CP/01")
    assert config.endpoint == "ws://host/base/ocpp/CP%2F01"

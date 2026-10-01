import asyncio
from types import SimpleNamespace

import pytest

from apps.ocpp.simulator.network import LiveSimulatorConfig, LiveSimulatorError
from apps.ocpp.simulator.transaction_worker import TransactionalLiveSimulatorWorker


class FakeMultiConnectorSimulator:
    def __init__(self):
        self.connected = False
        self.calls = []
        self.next_transaction_id = 100

    async def connect(self):
        self.connected = True

    async def close(self):
        self.connected = False

    async def reconnect(self):
        self.connected = True

    async def boot(self):
        return SimpleNamespace(
            status="Accepted",
            current_time="2026-10-01T04:00:00Z",
            interval=60,
        )

    async def call(self, action, payload):
        self.calls.append((action, dict(payload)))
        if action == "StartTransaction":
            transaction_id = self.next_transaction_id
            self.next_transaction_id += 1
            return {
                "transactionId": transaction_id,
                "idTagInfo": {"status": "Accepted"},
            }
        if action == "StopTransaction":
            return {"idTagInfo": {"status": "Accepted"}}
        return {}


def worker(tmp_path, fake):
    return TransactionalLiveSimulatorWorker(
        LiveSimulatorConfig(
            url="ws://example.test",
            charger="DUAL001",
            allow_insecure_ws=True,
            evidence_dir=str(tmp_path),
            heartbeat=False,
            connectors=2,
        ),
        simulator_factory=lambda config: fake,
    )


def test_start_without_connector_uses_lowest_available_connector(tmp_path):
    async def exercise():
        fake = FakeMultiConnectorSimulator()
        current = worker(tmp_path, fake)

        first = await current.dispatch({"action": "transaction-start", "id_tag": "A"})
        second = await current.dispatch({"action": "transaction-start", "id_tag": "B"})

        assert first["connector"] == 1
        assert second["connector"] == 2
        assert first["transaction_id"] == 100
        assert second["transaction_id"] == 101
        starts = [payload for action, payload in fake.calls if action == "StartTransaction"]
        assert [payload["connectorId"] for payload in starts] == [1, 2]

        with pytest.raises(LiveSimulatorError, match="no available connector"):
            await current.dispatch({"action": "transaction-start", "id_tag": "C"})

    asyncio.run(exercise())


def test_two_connectors_charge_independently_and_freed_connector_is_reused(tmp_path):
    async def exercise():
        fake = FakeMultiConnectorSimulator()
        current = worker(tmp_path, fake)
        first = await current.dispatch({"action": "transaction-start", "id_tag": "A"})
        second = await current.dispatch({"action": "transaction-start", "id_tag": "B"})

        await current.dispatch(
            {"action": "meter", "connector_id": 2, "energy_wh": 500, "power_w": 7000}
        )
        stopped = await current.dispatch(
            {"action": "transaction-stop", "connector_id": 1, "meter_stop": 250}
        )
        assert stopped["connector"] == 1

        status = await current.dispatch({"action": "status"})
        by_connector = {item["connector"]: item for item in status["connectors"]}
        assert by_connector[1]["status"] == "Available"
        assert by_connector[1]["transaction_id"] is None
        assert by_connector[2]["status"] == "Charging"
        assert by_connector[2]["transaction_id"] == second["transaction_id"]
        assert by_connector[2]["meter_wh"] == 500

        third = await current.dispatch({"action": "transaction-start", "id_tag": "C"})
        assert third["connector"] == 1
        assert third["transaction_id"] != first["transaction_id"]

    asyncio.run(exercise())


def test_meter_and_stop_require_connector_when_more_than_one_transaction_is_active(tmp_path):
    async def exercise():
        fake = FakeMultiConnectorSimulator()
        current = worker(tmp_path, fake)
        await current.dispatch({"action": "transaction-start", "id_tag": "A"})
        await current.dispatch({"action": "transaction-start", "id_tag": "B"})

        with pytest.raises(LiveSimulatorError, match="multiple active transactions"):
            await current.dispatch({"action": "meter"})
        with pytest.raises(LiveSimulatorError, match="multiple active transactions"):
            await current.dispatch({"action": "transaction-stop"})

    asyncio.run(exercise())


def test_boot_and_reconnect_announce_each_connector(tmp_path):
    async def exercise():
        fake = FakeMultiConnectorSimulator()
        current = worker(tmp_path, fake)
        await current.connect_and_boot()
        boot_statuses = [
            payload["connectorId"]
            for action, payload in fake.calls
            if action == "StatusNotification"
        ]
        assert boot_statuses == [1, 2]

        fake.calls.clear()
        await current.reconnect()
        reconnect_statuses = [
            payload["connectorId"]
            for action, payload in fake.calls
            if action == "StatusNotification"
        ]
        assert reconnect_statuses == [1, 2]

    asyncio.run(exercise())

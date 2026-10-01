import asyncio
from types import SimpleNamespace

import pytest

from apps.ocpp.simulator.network import LiveSimulatorConfig, LiveSimulatorError
from apps.ocpp.simulator.transaction_worker import TransactionalLiveSimulatorWorker


class FakeTransactionSimulator:
    def __init__(self):
        self.connected = False
        self.calls = []
        self.start_status = "Accepted"
        self.transaction_id = 41
        self.stop_error = None

    async def connect(self):
        self.connected = True

    async def close(self):
        self.connected = False

    async def boot(self):
        return SimpleNamespace(
            status="Accepted",
            current_time="2026-10-01T02:00:00Z",
            interval=60,
        )

    async def call(self, action, payload):
        self.calls.append((action, payload))
        if action == "StartTransaction":
            return {
                "transactionId": self.transaction_id,
                "idTagInfo": {"status": self.start_status},
            }
        if action == "StopTransaction":
            if self.stop_error is not None:
                raise self.stop_error
            return {"idTagInfo": {"status": "Accepted"}}
        return {}



def worker(tmp_path, fake):
    return TransactionalLiveSimulatorWorker(
        LiveSimulatorConfig(
            url="ws://example.test",
            charger="GW001",
            allow_insecure_ws=True,
            evidence_dir=str(tmp_path),
            heartbeat=False,
        ),
        simulator_factory=lambda config: fake,
    )


def test_boot_announces_available(tmp_path):
    async def exercise():
        fake = FakeTransactionSimulator()
        current = worker(tmp_path, fake)
        await current.connect_and_boot()

        assert fake.calls[0][0] == "StatusNotification"
        assert fake.calls[0][1]["status"] == "Available"
        assert (await current.dispatch({"action": "status"}))["status"] == "Available"

    asyncio.run(exercise())


def test_start_transaction_transitions_to_charging(tmp_path):
    async def exercise():
        fake = FakeTransactionSimulator()
        current = worker(tmp_path, fake)

        result = await current.dispatch(
            {
                "action": "transaction-start",
                "id_tag": "CARD-1",
                "connector_id": 1,
                "meter_start": 1200,
            }
        )

        assert result["started"] is True
        assert result["transaction_id"] == 41
        assert result["status"] == "Charging"
        assert result["meter_wh"] == 1200
        assert [action for action, _ in fake.calls] == [
            "StatusNotification",
            "StartTransaction",
            "StatusNotification",
        ]
        assert fake.calls[0][1]["status"] == "Preparing"
        assert fake.calls[1][1]["meterStart"] == 1200
        assert fake.calls[2][1]["status"] == "Charging"

    asyncio.run(exercise())


def test_rejected_start_returns_to_available(tmp_path):
    async def exercise():
        fake = FakeTransactionSimulator()
        fake.start_status = "Blocked"
        current = worker(tmp_path, fake)

        result = await current.dispatch(
            {"action": "transaction-start", "id_tag": "CARD-X"}
        )

        assert result["started"] is False
        assert result["authorization"] == "Blocked"
        assert result["transaction_id"] is None
        assert result["status"] == "Available"
        assert fake.calls[-1][1]["status"] == "Available"

    asyncio.run(exercise())


def test_stop_transaction_clears_state_only_after_csms_accepts_call(tmp_path):
    async def exercise():
        fake = FakeTransactionSimulator()
        current = worker(tmp_path, fake)
        await current.dispatch(
            {"action": "transaction-start", "id_tag": "CARD-1", "meter_start": 100}
        )

        result = await current.dispatch(
            {"action": "transaction-stop", "meter_stop": 250, "reason": "Local"}
        )

        assert result["stopped"] is True
        assert result["transaction_id"] == 41
        assert result["meter_wh"] == 250
        assert result["status"] == "Available"
        status = await current.dispatch({"action": "status"})
        assert status["transaction_id"] is None
        assert status["status"] == "Available"
        assert fake.calls[-3][1]["status"] == "Finishing"
        assert fake.calls[-2][0] == "StopTransaction"
        assert fake.calls[-2][1]["transactionId"] == 41
        assert fake.calls[-1][1]["status"] == "Available"

    asyncio.run(exercise())


def test_failed_stop_retains_active_transaction_and_charging_state(tmp_path):
    async def exercise():
        fake = FakeTransactionSimulator()
        current = worker(tmp_path, fake)
        await current.dispatch(
            {"action": "transaction-start", "id_tag": "CARD-1", "meter_start": 100}
        )
        fake.stop_error = LiveSimulatorError("transport failed")

        with pytest.raises(LiveSimulatorError, match="transport failed"):
            await current.dispatch(
                {"action": "transaction-stop", "meter_stop": 250}
            )

        status = await current.dispatch({"action": "status"})
        assert status["transaction_id"] == 41
        assert status["status"] == "Charging"
        assert status["meter_wh"] == 100
        assert fake.calls[-1][1]["status"] == "Charging"

    asyncio.run(exercise())


def test_second_transaction_is_rejected_while_one_is_active(tmp_path):
    async def exercise():
        fake = FakeTransactionSimulator()
        current = worker(tmp_path, fake)
        await current.dispatch({"action": "transaction-start", "id_tag": "CARD-1"})

        with pytest.raises(LiveSimulatorError, match="already has transaction"):
            await current.dispatch({"action": "transaction-start", "id_tag": "CARD-2"})

    asyncio.run(exercise())

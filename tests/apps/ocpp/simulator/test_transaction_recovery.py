import asyncio
import json
from types import SimpleNamespace

from apps.ocpp.simulator.network import LiveSimulatorConfig
from apps.ocpp.simulator.transaction_worker import TransactionalLiveSimulatorWorker


class FakeSimulator:
    def __init__(self):
        self.connected = False
        self.calls = []
        self.transaction_id = 77

    async def connect(self):
        self.connected = True

    async def close(self):
        self.connected = False

    async def boot(self):
        return SimpleNamespace(
            status="Accepted",
            current_time="2026-10-01T03:00:00Z",
            interval=60,
        )

    async def call(self, action, payload):
        self.calls.append((action, payload))
        if action == "StartTransaction":
            return {
                "transactionId": self.transaction_id,
                "idTagInfo": {"status": "Accepted"},
            }
        if action == "StopTransaction":
            return {"idTagInfo": {"status": "Accepted"}}
        return {}


def make_worker(tmp_path, fake):
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


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_transaction_operations_have_correlated_durable_results(tmp_path):
    async def exercise():
        fake = FakeSimulator()
        current = make_worker(tmp_path, fake)

        started = await current.dispatch(
            {"action": "transaction-start", "id_tag": "SECRET-CARD", "meter_start": 100}
        )
        metered = await current.dispatch(
            {"action": "meter", "energy_wh": 125, "power_w": 7200}
        )
        stopped = await current.dispatch({"action": "transaction-stop"})

        assert started["request_id"]
        assert metered["request_id"]
        assert stopped["request_id"]
        assert len({started["request_id"], metered["request_id"], stopped["request_id"]}) == 3

        results = read_jsonl(tmp_path / "results.jsonl")
        by_id = {item["request_id"]: item for item in results}
        assert by_id[started["request_id"]]["action"] == "StartTransaction"
        assert by_id[metered["request_id"]]["action"] == "MeterValues"
        assert by_id[stopped["request_id"]]["action"] == "StopTransaction"
        assert all(item["error"] is None for item in by_id.values())

    asyncio.run(exercise())


def test_transaction_evidence_never_persists_raw_id_tag(tmp_path):
    async def exercise():
        fake = FakeSimulator()
        current = make_worker(tmp_path, fake)
        await current.dispatch(
            {"action": "transaction-start", "id_tag": "SECRET-CARD", "meter_start": 100}
        )

    asyncio.run(exercise())

    assert "SECRET-CARD" not in (tmp_path / "events.jsonl").read_text()
    assert "SECRET-CARD" not in (tmp_path / "results.jsonl").read_text()


def test_worker_recovers_active_transaction_without_raw_id_tag(tmp_path):
    async def exercise():
        first_fake = FakeSimulator()
        first = make_worker(tmp_path, first_fake)
        await first.dispatch(
            {"action": "transaction-start", "id_tag": "SECRET-CARD", "meter_start": 400}
        )
        await first.dispatch(
            {"action": "meter", "energy_wh": 475, "power_w": 7000, "voltage_v": 240}
        )

        replacement_fake = FakeSimulator()
        recovered = make_worker(tmp_path, replacement_fake)
        status = recovered._transaction_status()
        assert status["transaction_id"] == 77
        assert status["status"] == "Charging"
        assert status["meter_wh"] == 475
        assert status["power_w"] == 7000
        assert status["voltage_v"] == 240
        assert recovered._transaction.id_tag is None

        await recovered.connect_and_boot()
        assert replacement_fake.calls[-1][0] == "StatusNotification"
        assert replacement_fake.calls[-1][1]["status"] == "Charging"

        stopped = await recovered.dispatch({"action": "transaction-stop"})
        assert stopped["stopped"] is True
        stop_payload = next(
            payload
            for action, payload in replacement_fake.calls
            if action == "StopTransaction"
        )
        assert "idTag" not in stop_payload

    asyncio.run(exercise())


def test_completed_stop_recovers_as_idle(tmp_path):
    async def exercise():
        first_fake = FakeSimulator()
        first = make_worker(tmp_path, first_fake)
        await first.dispatch(
            {"action": "transaction-start", "id_tag": "CARD-1", "meter_start": 10}
        )
        await first.dispatch({"action": "transaction-stop", "meter_stop": 20})

        recovered = make_worker(tmp_path, FakeSimulator())
        status = recovered._transaction_status()
        assert status["transaction_id"] is None
        assert status["status"] == "Available"
        assert status["meter_wh"] == 20

    asyncio.run(exercise())

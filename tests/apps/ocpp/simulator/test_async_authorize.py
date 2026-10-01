import asyncio
from types import SimpleNamespace

from apps.ocpp.simulator.network import LiveSimulatorConfig
from apps.ocpp.simulator.worker import LiveSimulatorWorker


class DelayedSimulator:
    def __init__(self):
        self.connected = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def connect(self):
        self.connected = True

    async def close(self):
        self.connected = False

    async def boot(self):
        return SimpleNamespace(
            status="Accepted",
            interval=0,
            current_time="2026-10-01T00:00:00Z",
        )

    async def authorize(self, id_tag):
        self.started.set()
        await self.release.wait()
        return "Accepted"


class FailingSimulator(DelayedSimulator):
    async def authorize(self, id_tag):
        raise RuntimeError("transport failed")


def make_worker(tmp_path, simulator):
    return LiveSimulatorWorker(
        LiveSimulatorConfig(
            url="ws://example.test",
            charger="GW001",
            allow_insecure_ws=True,
            evidence_dir=str(tmp_path),
        ),
        simulator_factory=lambda config: simulator,
    )


def test_wait_timeout_does_not_cancel_authorize(tmp_path):
    async def exercise():
        simulator = DelayedSimulator()
        worker = make_worker(tmp_path, simulator)
        await worker.connect_and_boot()

        submitted = await worker.dispatch(
            {"action": "authorize", "id_tag": "TAG-1"}
        )
        await simulator.started.wait()

        timeout = await worker.dispatch(
            {
                "action": "wait-result",
                "request_id": submitted["request_id"],
                "timeout": 0,
            }
        )
        assert timeout["timed_out"] is True
        assert timeout["completed"] is False

        simulator.release.set()
        completed = await worker.dispatch(
            {
                "action": "wait-result",
                "request_id": submitted["request_id"],
                "timeout": 1,
            }
        )
        assert completed["authorization"] == "Accepted"
        assert completed["completed"] is True

    asyncio.run(exercise())


def test_transport_failure_is_a_correlated_result(tmp_path):
    async def exercise():
        worker = make_worker(tmp_path, FailingSimulator())
        await worker.connect_and_boot()

        submitted = await worker.dispatch(
            {"action": "authorize", "id_tag": "TAG-2"}
        )
        completed = await worker.dispatch(
            {
                "action": "wait-result",
                "request_id": submitted["request_id"],
                "timeout": 1,
            }
        )

        assert completed["completed"] is True
        assert completed["authorization"] is None
        assert completed["error"] == "transport failed"

    asyncio.run(exercise())

import asyncio
import json
from types import SimpleNamespace

import pytest

from apps.ocpp.simulator.network import LiveSimulatorConfig, LiveSimulatorError
from apps.ocpp.simulator.worker import (
    DEFAULT_IDLE_TIMEOUT,
    LiveSimulatorWorker,
    active_session,
    runtime_dir,
    session_path,
)


class FakeSimulator:
    connected = True

    async def connect(self):
        self.connected = True

    async def close(self):
        self.connected = False

    async def boot(self):
        return SimpleNamespace(status="Accepted", interval=0)


def test_default_lifecycle_has_no_idle_shutdown():
    worker = LiveSimulatorWorker(
        LiveSimulatorConfig(
            url="ws://127.0.0.1:9000",
            charger="GW001",
            allow_insecure_ws=True,
        ),
        simulator_factory=lambda config: FakeSimulator(),
    )

    assert DEFAULT_IDLE_TIMEOUT == 0.0
    assert worker.idle_timeout == 0.0


def test_negative_idle_timeout_is_rejected():
    with pytest.raises(ValueError, match="zero or greater"):
        LiveSimulatorWorker(
            LiveSimulatorConfig(
                url="ws://127.0.0.1:9000",
                charger="GW001",
                allow_insecure_ws=True,
            ),
            idle_timeout=-1,
            simulator_factory=lambda config: FakeSimulator(),
        )


def test_active_session_returns_single_live_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("OCPP_SIMULATOR_RUNTIME_DIR", str(tmp_path))
    path = session_path("GW001")
    path.write_text(
        json.dumps(
            {
                "charger": "GW001",
                "url": "ws://127.0.0.1:9000",
                "pid": 123,
                "socket": str(runtime_dir() / "sim.sock"),
            }
        )
    )
    monkeypatch.setattr("apps.ocpp.simulator.worker._pid_alive", lambda pid: True)

    assert active_session()["charger"] == "GW001"


def test_active_session_rejects_multiple_live_workers(tmp_path, monkeypatch):
    monkeypatch.setenv("OCPP_SIMULATOR_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr("apps.ocpp.simulator.worker._pid_alive", lambda pid: True)
    for index, charger in enumerate(("GW001", "GW002"), start=1):
        session_path(charger).write_text(
            json.dumps(
                {
                    "charger": charger,
                    "url": f"ws://127.0.0.1:{9000 + index}",
                    "pid": index,
                    "socket": str(runtime_dir() / f"{charger}.sock"),
                }
            )
        )

    with pytest.raises(LiveSimulatorError, match="multiple live simulator sessions"):
        active_session()


def test_status_reports_on_demand_lifecycle():
    async def exercise():
        fake = FakeSimulator()
        worker = LiveSimulatorWorker(
            LiveSimulatorConfig(
                url="ws://127.0.0.1:9000",
                charger="GW001",
                allow_insecure_ws=True,
            ),
            simulator_factory=lambda config: fake,
        )
        await worker.connect_and_boot()
        response = await worker.dispatch({"action": "status"})
        assert response["lifecycle"] == "on-demand"
        assert response["idle_timeout"] == 0.0

    asyncio.run(exercise())

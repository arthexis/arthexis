import asyncio
from types import SimpleNamespace

from apps.ocpp.simulator.network import LiveSimulatorConfig
from apps.ocpp.simulator.worker import LiveSimulatorWorker, runtime_dir


def insecure_config(**kwargs):
    return LiveSimulatorConfig(allow_insecure_ws=True, **kwargs)


class FakeSimulator:
    def __init__(self, *, authorization="Accepted") -> None:
        self.connected = False
        self.authorization = authorization
        self.reconnects = 0
        self.calls = []
        self.heartbeat_started = None
        self.release_heartbeat = None

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def reconnect(self) -> None:
        self.reconnects += 1
        self.connected = True

    async def boot(self):
        return SimpleNamespace(status="Accepted", interval=60)

    async def authorize(self, id_tag):
        self.calls.append(("Authorize", id_tag))
        return self.authorization

    async def call(self, action, payload):
        self.calls.append((action, payload))
        if action == "Heartbeat" and self.heartbeat_started is not None:
            self.heartbeat_started.set()
            await self.release_heartbeat.wait()
        return {"currentTime": "now"}


def worker_with(fake):
    return LiveSimulatorWorker(
        insecure_config(url="ws://example.test", charger="GWAY001"),
        simulator_factory=lambda config: fake,
    )


def test_worker_authorize_reuses_live_connection():
    async def exercise():
        fake = FakeSimulator(authorization="Invalid")
        worker = worker_with(fake)
        await worker.connect_and_boot()

        response = await worker.dispatch(
            {"action": "authorize", "id_tag": "UNKNOWN001"}
        )

        assert response["authorization"] == "Invalid"
        assert fake.calls == [("Authorize", "UNKNOWN001")]

    asyncio.run(exercise())


def test_worker_reconnect_reboots_same_charger():
    async def exercise():
        fake = FakeSimulator()
        worker = worker_with(fake)
        await worker.connect_and_boot()

        response = await worker.dispatch({"action": "reconnect"})

        assert response["reconnects"] == 1
        assert fake.reconnects == 1
        assert fake.connected is True

    asyncio.run(exercise())


def test_worker_close_returns_closed_result():
    async def exercise():
        worker = worker_with(FakeSimulator())
        response = await worker.dispatch({"action": "close"})
        assert response == {"ok": True, "charger": "GWAY001", "closed": True}

    asyncio.run(exercise())


def test_runtime_directory_is_owner_only(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    root.mkdir(mode=0o777)
    monkeypatch.setenv("OCPP_SIMULATOR_RUNTIME_DIR", str(root))
    secured = runtime_dir()
    assert secured.stat().st_mode & 0o777 == 0o700


def test_reconnect_waits_for_inflight_heartbeat():
    async def exercise():
        fake = FakeSimulator()
        fake.heartbeat_started = asyncio.Event()
        fake.release_heartbeat = asyncio.Event()
        worker = worker_with(fake)
        await worker.connect_and_boot()

        heartbeat_task = asyncio.create_task(worker.heartbeat())
        await fake.heartbeat_started.wait()
        reconnect_task = asyncio.create_task(worker.dispatch({"action": "reconnect"}))

        await asyncio.sleep(0)
        assert fake.reconnects == 0

        fake.release_heartbeat.set()
        await heartbeat_task
        response = await reconnect_task

        assert fake.calls == [("Heartbeat", {})]
        assert fake.reconnects == 1
        assert response["reconnects"] == 1

    asyncio.run(exercise())


def test_worker_authorization_scenario_returns_privacy_safe_matrix():
    class MatrixSimulator(FakeSimulator):
        def __init__(self):
            super().__init__()
            self.outcomes = iter(["Accepted", "Blocked", "Invalid", "Accepted"])

        async def authorize(self, id_tag):
            self.calls.append(("Authorize", id_tag))
            return next(self.outcomes)

    async def exercise():
        fake = MatrixSimulator()
        worker = worker_with(fake)
        await worker.connect_and_boot()

        response = await worker.dispatch(
            {
                "action": "authorize-scenario",
                "policy_context": "restricted",
                "known_authorized": "KNOWN-OK",
                "known_denied": "KNOWN-NO",
                "unknown": "UNKNOWN",
            }
        )

        assert response["scenario"] == "restricted-authorization-matrix"
        assert response["policy_context"] == "restricted"
        assert [item["status"] for item in response["results"]] == [
            "Accepted",
            "Blocked",
            "Invalid",
            "Accepted",
        ]
        assert [item["attempt"] for item in response["results"]] == [
            "known-authorized",
            "known-denied",
            "unknown",
            "known-authorized-repeat",
        ]
        assert all("id_tag" not in item for item in response["results"])
        assert fake.calls == [
            ("Authorize", "KNOWN-OK"),
            ("Authorize", "KNOWN-NO"),
            ("Authorize", "UNKNOWN"),
            ("Authorize", "KNOWN-OK"),
        ]

    asyncio.run(exercise())

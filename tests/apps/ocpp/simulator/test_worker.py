import asyncio
from pathlib import Path
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


def test_worker_replay_resolves_source_and_runs_live_transport(monkeypatch, tmp_path):
    async def exercise():
        fake = FakeSimulator()
        worker = worker_with(fake)
        await worker.connect_and_boot()

        source = SimpleNamespace(
            database=tmp_path / "replay.sqlite3",
            kind="package",
            capture_id="capture-123",
        )
        seen = {}

        def fake_resolve(path):
            seen["source"] = path
            return source

        def fake_events(database, *, charger_identity, batch_size):
            seen["database"] = database
            seen["source_charger"] = charger_identity
            seen["batch_size"] = batch_size
            return ("lazy-events",)

        async def fake_run(
            transport,
            events,
            *,
            reconnect_after,
            pacing,
            metrics,
            max_retained_actions,
        ):
            seen["transport"] = transport
            seen["max_retained_actions"] = max_retained_actions
            seen["metrics"] = metrics
            seen["events"] = events
            seen["reconnect_after"] = reconnect_after
            seen["pacing"] = pacing
            metrics.attempted_requests = 2
            metrics.completed_requests = 2
            metrics.elapsed_seconds = 0.5
            metrics.total_latency_seconds = 0.2
            metrics.max_latency_seconds = 0.15
            return ("StartTransaction", "MeterValues")

        monkeypatch.setattr(
            "apps.ocpp.simulator.worker.resolve_replay_database",
            fake_resolve,
        )
        monkeypatch.setattr(
            "apps.ocpp.simulator.worker.iter_v16_transaction_replay",
            fake_events,
        )
        monkeypatch.setattr(
            "apps.ocpp.simulator.worker.run_v16_live_replay_events",
            fake_run,
        )

        response = await worker.dispatch(
            {
                "action": "replay",
                "source": str(tmp_path / "package"),
                "source_charger": "field-charger",
                "stream": "transactions",
                "batch_size": 50,
                "pacing": "burst",
                "interval_seconds": 0.25,
                "burst_size": 10,
                "burst_pause_seconds": 1.5,
                "reconnect_after": 25,
            }
        )

        assert seen["source"] == Path(tmp_path / "package")
        assert seen["database"] == source.database
        assert seen["source_charger"] == "field-charger"
        assert seen["batch_size"] == 50
        assert seen["transport"] is fake
        assert seen["events"] == ("lazy-events",)
        assert seen["reconnect_after"] == 25
        assert seen["pacing"].mode == "burst"
        assert seen["pacing"].burst_size == 10
        assert seen["pacing"].burst_pause_seconds == 1.5
        assert seen["max_retained_actions"] == 1000
        assert response == {
            "ok": True,
            "charger": "GWAY001",
            "source_kind": "package",
            "capture_id": "capture-123",
            "source_charger": "field-charger",
            "stream": "transactions",
            "events_completed": 2,
            "actions": ["StartTransaction", "MeterValues"],
            "actions_truncated": False,
            "pacing": "burst",
            "reconnect_after": 25,
            "metrics": {
                "attempted_requests": 2,
                "completed_requests": 2,
                "failed_requests": 0,
                "transport_failures": 0,
                "reconnect_attempts": 0,
                "reconnect_successes": 0,
                "reconnect_failures": 0,
                "elapsed_seconds": 0.5,
                "throughput_requests_per_second": 4.0,
                "mean_latency_seconds": 0.1,
                "max_latency_seconds": 0.15,
                "mean_reconnect_seconds": 0.0,
                "max_reconnect_seconds": 0.0,
                "error_counts": {},
            },
        }

    asyncio.run(exercise())


def test_worker_replay_selects_retained_inbound_stream(monkeypatch, tmp_path):
    async def exercise():
        fake = FakeSimulator()
        worker = worker_with(fake)
        await worker.connect_and_boot()

        source = SimpleNamespace(
            database=tmp_path / "replay.sqlite3",
            kind="database",
            capture_id=None,
        )
        seen = {}

        monkeypatch.setattr(
            "apps.ocpp.simulator.worker.resolve_replay_database",
            lambda path: source,
        )

        def unexpected_transactions(*args, **kwargs):
            raise AssertionError("transaction stream should not be selected")

        def fake_inbound(database, *, charger_identity, batch_size):
            seen["database"] = database
            seen["source_charger"] = charger_identity
            seen["batch_size"] = batch_size
            return ("inbound-events",)

        async def fake_run(
            transport,
            events,
            *,
            reconnect_after,
            pacing,
            metrics,
            max_retained_actions,
        ):
            seen["events"] = events
            seen["max_retained_actions"] = max_retained_actions
            return ("Authorize",)

        monkeypatch.setattr(
            "apps.ocpp.simulator.worker.iter_v16_transaction_replay",
            unexpected_transactions,
        )
        monkeypatch.setattr(
            "apps.ocpp.simulator.worker.iter_v16_inbound_request_replay",
            fake_inbound,
        )
        monkeypatch.setattr(
            "apps.ocpp.simulator.worker.run_v16_live_replay_events",
            fake_run,
        )

        response = await worker.dispatch(
            {
                "action": "replay",
                "source": str(tmp_path / "replay.sqlite3"),
                "source_charger": "field-charger",
                "stream": "inbound",
                "batch_size": 25,
                "pacing": "maximum",
            }
        )

        assert seen["database"] == source.database
        assert seen["source_charger"] == "field-charger"
        assert seen["batch_size"] == 25
        assert seen["events"] == ("inbound-events",)
        assert seen["max_retained_actions"] == 1000
        assert response["stream"] == "inbound"
        assert response["actions"] == ["Authorize"]
        assert response["actions_truncated"] is False

    asyncio.run(exercise())

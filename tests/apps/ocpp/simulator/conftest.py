from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from apps.ocpp.simulator.network import LiveSimulatorConfig
from apps.ocpp.simulator.transaction_worker import TransactionalLiveSimulatorWorker


class ManualClock:
    def __init__(self, start: datetime | None = None):
        self.current = start or datetime(2026, 10, 1, 2, 0, tzinfo=timezone.utc)

    def now(self):
        return self.current

    def isoformat(self):
        return self.current.isoformat().replace("+00:00", "Z")

    def describe(self):
        return {
            "mode": "manual",
            "offset_seconds": 0.0,
            "start_time": self.isoformat(),
            "charger_time": self.isoformat(),
        }

    def advance(self, seconds):
        self.current += timedelta(seconds=seconds)


class FakeTransactionPeer:
    """Configurable in-memory OCPP peer for transaction worker tests."""

    def __init__(self, *, transaction_id: int = 41, increment_ids: bool = False):
        self.connected = False
        self.calls: list[tuple[str, dict]] = []
        self.transaction_id = transaction_id
        self.increment_ids = increment_ids
        self.start_status = "Accepted"
        self.stop_status = "Accepted"
        self.stop_error = None
        self.meter_error = None

    async def connect(self):
        self.connected = True

    async def close(self):
        self.connected = False

    async def reconnect(self):
        self.connected = True

    async def boot(self):
        return SimpleNamespace(
            status="Accepted",
            current_time="2026-10-01T02:00:00Z",
            interval=60,
        )

    async def call(self, action, payload):
        self.calls.append((action, dict(payload)))
        if action == "StartTransaction":
            transaction_id = self.transaction_id
            if self.increment_ids:
                self.transaction_id += 1
            return {
                "transactionId": transaction_id,
                "idTagInfo": {"status": self.start_status},
            }
        if action == "MeterValues" and self.meter_error is not None:
            raise self.meter_error
        if action == "StopTransaction":
            if self.stop_error is not None:
                raise self.stop_error
            return {"idTagInfo": {"status": self.stop_status}}
        return {}

    def payloads(self, action: str) -> list[dict]:
        return [payload for seen_action, payload in self.calls if seen_action == action]


@pytest.fixture
def peer_factory():
    def create(**kwargs):
        return FakeTransactionPeer(**kwargs)

    return create


@pytest.fixture
def worker_factory(tmp_path):
    def create(
        peer: FakeTransactionPeer,
        *,
        connectors: int = 1,
        charger: str = "GW001",
    ) -> TransactionalLiveSimulatorWorker:
        return TransactionalLiveSimulatorWorker(
            LiveSimulatorConfig(
                url="ws://example.test",
                charger=charger,
                allow_insecure_ws=True,
                evidence_dir=str(tmp_path),
                heartbeat=False,
                connectors=connectors,
            ),
            simulator_factory=lambda config: peer,
        )

    return create


@pytest.fixture
def manual_clock():
    return ManualClock()


@pytest.fixture
def use_manual_clock(manual_clock):
    def attach(worker: TransactionalLiveSimulatorWorker):
        worker._clock = manual_clock
        for state in worker._connectors.values():
            state.meter_anchor = manual_clock.now()
        return manual_clock

    return attach

"""Tests for migrated-database replay sources."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from asgiref.sync import async_to_sync

import apps.ocpp.simulator.database_replay as database_replay
from apps.ocpp.simulator.database_replay import (
    ReplayMetrics,
    ReplayPacing,
    iter_v16_inbound_request_replay,
    iter_v16_transaction_replay,
    ReplayEvent,
    run_v16_database_replay,
    run_v16_live_replay_events,
    run_v16_replay_events,
)
from tests.apps.ocpp.builders import charger


class FakeReplayTransport:
    def __init__(self, *, transaction_id=91, boot_status="Accepted"):
        self.transaction_id = transaction_id
        self.boot_status = boot_status
        self.calls = []
        self.reconnects = 0
        self.boots = 0

    async def call(self, action, payload):
        self.calls.append((action, dict(payload)))
        if action == "StartTransaction":
            return {"transactionId": self.transaction_id}
        return {}

    async def reconnect(self):
        self.reconnects += 1

    async def boot(self):
        self.boots += 1
        return type("Boot", (), {"status": self.boot_status})()


def replay_events():
    return (
        ReplayEvent(
            source_transaction_id=10,
            occurred_at="2024-01-01T00:00:00+00:00",
            action="StartTransaction",
            payload={"connectorId": 1, "idTag": "tag-a", "meterStart": 0},
        ),
        ReplayEvent(
            source_transaction_id=10,
            occurred_at="2024-01-01T00:10:00+00:00",
            action="MeterValues",
            payload={"meterValue": []},
            requires_runtime_transaction_id=True,
        ),
        ReplayEvent(
            source_transaction_id=10,
            occurred_at="2024-01-01T01:00:00+00:00",
            action="StopTransaction",
            payload={"meterStop": 10},
            requires_runtime_transaction_id=True,
        ),
    )


def _database(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE base_schemageneration (
                id INTEGER PRIMARY KEY,
                generation INTEGER NOT NULL
            );
            INSERT INTO base_schemageneration(generation) VALUES (2);

            CREATE TABLE ocpp_charger (
                id INTEGER PRIMARY KEY,
                identity TEXT NOT NULL
            );
            CREATE TABLE ocpp_connector (
                id INTEGER PRIMARY KEY,
                charger_id INTEGER NOT NULL,
                number INTEGER NOT NULL
            );
            CREATE TABLE ocpp_ocpptransaction (
                id INTEGER PRIMARY KEY,
                charger_id INTEGER NOT NULL,
                connector_id INTEGER,
                id_tag TEXT,
                started_at TEXT NOT NULL,
                stopped_at TEXT,
                meter_start TEXT,
                meter_stop TEXT
            );
            CREATE TABLE ocpp_metervalue (
                id INTEGER PRIMARY KEY,
                transaction_id INTEGER NOT NULL,
                sampled_at TEXT NOT NULL,
                value TEXT NOT NULL,
                measurand TEXT NOT NULL,
                unit TEXT NOT NULL,
                multiplier INTEGER NOT NULL
            );
            CREATE TABLE ocpp_inboundprotocolrequest (
                id INTEGER PRIMARY KEY,
                charger_id INTEGER NOT NULL,
                version TEXT NOT NULL,
                direction TEXT NOT NULL,
                action TEXT NOT NULL,
                request_payload TEXT NOT NULL,
                received_at TEXT NOT NULL
            );

            INSERT INTO ocpp_charger(id, identity) VALUES
                (1, 'charger-a'),
                (2, 'charger-b');
            INSERT INTO ocpp_connector(id, charger_id, number) VALUES
                (1, 1, 1),
                (2, 2, 2);

            INSERT INTO ocpp_ocpptransaction(
                id, charger_id, connector_id, id_tag,
                started_at, stopped_at, meter_start, meter_stop
            ) VALUES
                (10, 1, 1, 'tag-a', '2024-01-01T00:00:00+00:00',
                 '2024-01-01T01:00:00+00:00', '100', '300'),
                (20, 2, 2, 'tag-b', '2024-02-01T00:00:00+00:00',
                 NULL, '500', NULL);

            INSERT INTO ocpp_metervalue(
                id, transaction_id, sampled_at, value, measurand, unit, multiplier
            ) VALUES
                (1, 10, '2024-01-01T00:10:00+00:00', '150',
                 'Energy.Active.Import.Register', 'Wh', 0),
                (2, 10, '2024-01-01T00:20:00+00:00', '200',
                 'Energy.Active.Import.Register', 'Wh', 0);

            INSERT INTO ocpp_inboundprotocolrequest(
                id, charger_id, version, direction, action, request_payload, received_at
            ) VALUES
                (100, 1, 'ocpp1.6', 'charge_point_to_csms', 'Authorize',
                 '{"idTag":"tag-a"}', '2024-01-01T00:00:01+00:00'),
                (101, 1, 'ocpp1.6', 'csms_to_charge_point', 'Reset',
                 '{"type":"Soft"}', '2024-01-01T00:00:02+00:00'),
                (102, 2, 'ocpp2.0.1', 'charge_point_to_csms', 'Heartbeat',
                 '{}', '2024-02-01T00:00:01+00:00');
            """
        )
    return path


def test_database_replay_preserves_transaction_and_meter_order(tmp_path) -> None:
    database = _database(tmp_path / "replay.sqlite3")

    events = list(
        iter_v16_transaction_replay(
            database,
            charger_identity="charger-a",
            batch_size=1,
        )
    )

    assert [event.action for event in events] == [
        "StartTransaction",
        "MeterValues",
        "MeterValues",
        "StopTransaction",
    ]
    assert [event.occurred_at for event in events] == sorted(
        event.occurred_at for event in events
    )
    assert events[0].payload["connectorId"] == 1
    assert events[0].payload["idTag"] == "tag-a"
    assert events[0].requires_runtime_transaction_id is False
    assert all(event.requires_runtime_transaction_id for event in events[1:])


def test_database_replay_filters_charger_and_leaves_open_transaction_open(tmp_path) -> None:
    database = _database(tmp_path / "replay.sqlite3")

    events = list(iter_v16_transaction_replay(database, charger_identity="charger-b"))

    assert [event.action for event in events] == ["StartTransaction"]
    assert events[0].source_transaction_id == 20


def test_database_replay_requires_current_generation_database(tmp_path) -> None:
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE something_old (id INTEGER PRIMARY KEY)")

    with pytest.raises(ValueError, match="current-generation"):
        list(iter_v16_transaction_replay(database))


@pytest.mark.django_db
def test_maximum_speed_replay_rebinds_runtime_transaction_id(tmp_path) -> None:
    database = _database(tmp_path / "replay.sqlite3")
    target = charger("replay-target", authorization_mode="open")

    completed = async_to_sync(run_v16_database_replay)(
        target,
        database,
        source_charger_identity="charger-a",
        batch_size=1,
    )

    assert completed == (
        "StartTransaction",
        "MeterValues",
        "MeterValues",
        "StopTransaction",
    )
    transaction = target.transactions.get()
    assert transaction.stopped_at is not None
    assert transaction.meter_values.count() == 2


def test_inbound_request_replay_only_returns_v16_charger_originated_requests(tmp_path) -> None:
    database = _database(tmp_path / "replay.sqlite3")

    events = list(
        iter_v16_inbound_request_replay(
            database,
            charger_identity="charger-a",
            batch_size=1,
        )
    )

    assert [event.action for event in events] == ["Authorize"]
    assert events[0].payload == {"idTag": "tag-a"}


@pytest.mark.parametrize(
    "pacing",
    [
        ReplayPacing(mode="unknown"),
        ReplayPacing(mode="fixed", interval_seconds=-1),
        ReplayPacing(mode="burst", burst_size=0),
    ],
)
def test_replay_pacing_rejects_invalid_configuration(pacing: ReplayPacing) -> None:
    with pytest.raises(ValueError):
        pacing.validate()


@pytest.mark.django_db
def test_database_replay_reconnects_and_resumes_mid_transaction(tmp_path, monkeypatch) -> None:
    database = _database(tmp_path / "replay.sqlite3")
    target = charger("replay-reconnect-target", authorization_mode="open")
    real_simulator = database_replay.OcppSimulator
    instances = []

    def build_simulator(*, charger, version):
        simulator = real_simulator(charger=charger, version=version)
        instances.append(simulator)
        return simulator

    monkeypatch.setattr(database_replay, "OcppSimulator", build_simulator)

    completed = async_to_sync(run_v16_database_replay)(
        target,
        database,
        source_charger_identity="charger-a",
        batch_size=1,
        reconnect_after=2,
    )

    assert completed == (
        "StartTransaction",
        "MeterValues",
        "MeterValues",
        "StopTransaction",
    )
    assert len(instances) == 2
    assert target.transactions.count() == 1
    transaction = target.transactions.get()
    assert transaction.stopped_at is not None
    assert transaction.meter_values.count() == 2


@pytest.mark.django_db
def test_database_replay_rejects_invalid_reconnect_checkpoint(tmp_path) -> None:
    database = _database(tmp_path / "replay.sqlite3")
    target = charger("replay-reconnect-invalid", authorization_mode="open")

    with pytest.raises(ValueError, match="reconnect_after must be positive"):
        async_to_sync(run_v16_database_replay)(
            target,
            database,
            source_charger_identity="charger-a",
            reconnect_after=0,
        )


def test_transport_neutral_replay_preserves_order_and_rebinds_transaction_id():
    async def exercise():
        transport = FakeReplayTransport(transaction_id=91)

        completed = await run_v16_replay_events(transport, replay_events())

        assert completed == (
            "StartTransaction",
            "MeterValues",
            "StopTransaction",
        )
        assert [action for action, _ in transport.calls] == list(completed)
        assert transport.calls[1][1]["transactionId"] == 91
        assert transport.calls[2][1]["transactionId"] == 91

    async_to_sync(exercise)()


def test_transport_neutral_replay_rejects_missing_runtime_transaction_id():
    async def exercise():
        event = ReplayEvent(
            source_transaction_id=10,
            occurred_at="2024-01-01T00:10:00+00:00",
            action="MeterValues",
            payload={"meterValue": []},
            requires_runtime_transaction_id=True,
        )
        with pytest.raises(ValueError, match="runtime transaction ID"):
            await run_v16_replay_events(FakeReplayTransport(), (event,))

    async_to_sync(exercise)()


def test_live_replay_reconnects_reboots_and_resumes_same_transaction():
    async def exercise():
        transport = FakeReplayTransport(transaction_id=77)

        completed = await run_v16_live_replay_events(
            transport,
            replay_events(),
            reconnect_after=1,
        )

        assert completed == (
            "StartTransaction",
            "MeterValues",
            "StopTransaction",
        )
        assert transport.reconnects == 1
        assert transport.boots == 1
        assert transport.calls[1][1]["transactionId"] == 77
        assert transport.calls[2][1]["transactionId"] == 77

    async_to_sync(exercise)()


def test_live_replay_rejects_nonaccepted_boot_after_reconnect():
    async def exercise():
        with pytest.raises(ValueError, match="not accepted"):
            await run_v16_live_replay_events(
                FakeReplayTransport(boot_status="Pending"),
                replay_events(),
                reconnect_after=1,
            )

    async_to_sync(exercise)()


def test_live_replay_rejects_invalid_reconnect_checkpoint():
    async def exercise():
        with pytest.raises(ValueError, match="reconnect_after must be positive"):
            await run_v16_live_replay_events(
                FakeReplayTransport(),
                (),
                reconnect_after=0,
            )

    async_to_sync(exercise)()


def test_live_replay_collects_bounded_latency_and_throughput_metrics():
    async def exercise():
        ticks = iter((1.0, 1.1, 2.0, 2.2, 3.0, 3.3, 4.0))
        metrics = ReplayMetrics(_clock=lambda: next(ticks), _started_at=0.0)

        completed = await run_v16_live_replay_events(
            FakeReplayTransport(transaction_id=77),
            replay_events(),
            metrics=metrics,
        )

        assert completed == (
            "StartTransaction",
            "MeterValues",
            "StopTransaction",
        )
        summary = metrics.as_dict()
        assert summary["attempted_requests"] == 3
        assert summary["completed_requests"] == 3
        assert summary["failed_requests"] == 0
        assert summary["transport_failures"] == 0
        assert summary["reconnect_attempts"] == 0
        assert summary["reconnect_successes"] == 0
        assert summary["reconnect_failures"] == 0
        assert summary["elapsed_seconds"] == pytest.approx(4.0)
        assert summary["throughput_requests_per_second"] == pytest.approx(0.75)
        assert summary["mean_latency_seconds"] == pytest.approx(0.2)
        assert summary["max_latency_seconds"] == pytest.approx(0.3)
        assert summary["mean_reconnect_seconds"] == 0.0
        assert summary["max_reconnect_seconds"] == 0.0
        assert summary["error_counts"] == {}

    async_to_sync(exercise)()


def test_live_replay_counts_transport_errors_without_retaining_samples():
    class FailingTransport(FakeReplayTransport):
        async def call(self, action, payload):
            if action == "MeterValues":
                raise TimeoutError("simulated timeout")
            return await super().call(action, payload)

    async def exercise():
        ticks = iter((1.0, 1.1, 2.0, 2.4, 2.5))
        metrics = ReplayMetrics(_clock=lambda: next(ticks), _started_at=0.0)

        with pytest.raises(TimeoutError, match="simulated timeout"):
            await run_v16_live_replay_events(
                FailingTransport(),
                replay_events(),
                metrics=metrics,
            )

        summary = metrics.as_dict()
        assert summary["attempted_requests"] == 2
        assert summary["completed_requests"] == 1
        assert summary["failed_requests"] == 1
        assert summary["transport_failures"] == 1
        assert summary["elapsed_seconds"] == pytest.approx(2.5)
        assert summary["mean_latency_seconds"] == pytest.approx(0.25)
        assert summary["max_latency_seconds"] == pytest.approx(0.4)
        assert summary["error_counts"] == {"TimeoutError": 1}

    async_to_sync(exercise)()



def test_live_replay_measures_successful_reconnect_cycle():
    async def exercise():
        ticks = iter((1.0, 1.1, 2.0, 2.5, 3.0, 3.2, 4.0, 4.3, 5.0))
        metrics = ReplayMetrics(_clock=lambda: next(ticks), _started_at=0.0)
        transport = FakeReplayTransport(transaction_id=77)

        completed = await run_v16_live_replay_events(
            transport,
            replay_events(),
            reconnect_after=1,
            metrics=metrics,
        )

        assert completed == (
            "StartTransaction",
            "MeterValues",
            "StopTransaction",
        )
        summary = metrics.as_dict()
        assert transport.reconnects == 1
        assert transport.boots == 1
        assert summary["reconnect_attempts"] == 1
        assert summary["reconnect_successes"] == 1
        assert summary["reconnect_failures"] == 0
        assert summary["mean_reconnect_seconds"] == pytest.approx(0.5)
        assert summary["max_reconnect_seconds"] == pytest.approx(0.5)

    async_to_sync(exercise)()


def test_live_replay_measures_failed_reconnect_boot():
    async def exercise():
        ticks = iter((1.0, 1.1, 2.0, 2.4, 2.5))
        metrics = ReplayMetrics(_clock=lambda: next(ticks), _started_at=0.0)

        with pytest.raises(ValueError, match="not accepted"):
            await run_v16_live_replay_events(
                FakeReplayTransport(boot_status="Pending"),
                replay_events(),
                reconnect_after=1,
                metrics=metrics,
            )

        summary = metrics.as_dict()
        assert summary["attempted_requests"] == 1
        assert summary["completed_requests"] == 1
        assert summary["reconnect_attempts"] == 1
        assert summary["reconnect_successes"] == 0
        assert summary["reconnect_failures"] == 1
        assert summary["mean_reconnect_seconds"] == pytest.approx(0.4)
        assert summary["max_reconnect_seconds"] == pytest.approx(0.4)
        assert summary["error_counts"] == {"ValueError": 1}

    async_to_sync(exercise)()



def test_live_replay_handles_100k_lazy_events_with_bounded_retained_state():
    class CountingTransport:
        def __init__(self):
            self.calls = 0
            self.reconnects = 0
            self.boots = 0

        async def call(self, action, payload):
            self.calls += 1
            return {}

        async def reconnect(self):
            self.reconnects += 1

        async def boot(self):
            self.boots += 1
            return type("Boot", (), {"status": "Accepted"})()

    def events():
        for sequence in range(100_000):
            yield ReplayEvent(
                source_transaction_id=sequence,
                occurred_at=f"synthetic-{sequence}",
                action="Heartbeat",
                payload={},
            )

    async def exercise():
        transport = CountingTransport()
        metrics = ReplayMetrics()

        completed = await run_v16_live_replay_events(
            transport,
            events(),
            reconnect_after=50_000,
            metrics=metrics,
            max_retained_actions=25,
        )

        assert transport.calls == 100_000
        assert transport.reconnects == 1
        assert transport.boots == 1
        assert metrics.attempted_requests == 100_000
        assert metrics.completed_requests == 100_000
        assert metrics.failed_requests == 0
        assert metrics.reconnect_successes == 1
        assert len(completed) == 25
        assert completed == ("Heartbeat",) * 25

    async_to_sync(exercise)()


@pytest.mark.parametrize("limit", [-1, -100])
def test_live_replay_rejects_negative_retained_action_limit(limit):
    async def exercise():
        with pytest.raises(ValueError, match="max_retained_actions"):
            await run_v16_live_replay_events(
                FakeReplayTransport(),
                (),
                max_retained_actions=limit,
            )

    async_to_sync(exercise)()

"""Tests for migrated-database replay sources."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from apps.ocpp.simulator.database_replay import iter_v16_transaction_replay


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
            """
        )
    return path


def test_database_replay_preserves_transaction_and_meter_order(tmp_path):
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
    assert all(
        event.requires_runtime_transaction_id
        for event in events[1:]
    )


def test_database_replay_filters_charger_and_leaves_open_transaction_open(tmp_path):
    database = _database(tmp_path / "replay.sqlite3")

    events = list(
        iter_v16_transaction_replay(database, charger_identity="charger-b")
    )

    assert [event.action for event in events] == ["StartTransaction"]
    assert events[0].source_transaction_id == 20


def test_database_replay_requires_current_generation_database(tmp_path):
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE something_old (id INTEGER PRIMARY KEY)")

    try:
        list(iter_v16_transaction_replay(database))
    except ValueError as error:
        assert "current-generation" in str(error)
    else:
        raise AssertionError("legacy database should not be accepted for replay")

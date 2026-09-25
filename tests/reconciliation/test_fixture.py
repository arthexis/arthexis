"""Tests for database-only migration fixtures."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from arthexis.reconciliation.capture import capture_legacy_installation
from arthexis.reconciliation.fixture import FIXTURE_FORMAT, restore_fixture


def _capture(tmp_path: Path):
    source = tmp_path / "legacy"
    source.mkdir()
    database = source / "db.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE django_migrations "
            "(id INTEGER PRIMARY KEY, app TEXT, name TEXT, applied TEXT)"
        )
        connection.execute(
            "INSERT INTO django_migrations(app, name, applied) "
            "VALUES ('core', '0001_initial', '2024-01-01')"
        )
        connection.execute("CREATE TABLE core_rfid (id INTEGER PRIMARY KEY, rfid TEXT)")
        connection.execute("INSERT INTO core_rfid(rfid) VALUES ('legacy-card')")
    return capture_legacy_installation(
        source,
        tmp_path / "captures",
        now=datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc),
    )


def test_restore_fixture_copies_only_database_and_provenance(tmp_path):
    capture = _capture(tmp_path)

    fixture = restore_fixture(
        capture.path,
        tmp_path / "fixtures",
        now=datetime(2026, 9, 25, 14, 5, tzinfo=timezone.utc),
    )

    assert fixture.database_path.is_file()
    assert fixture.metadata_path.is_file()
    assert sorted(path.name for path in fixture.path.iterdir()) == [
        "database.sqlite3",
        "fixture.json",
    ]

    metadata = json.loads(fixture.metadata_path.read_text(encoding="utf-8"))
    assert metadata["format"] == FIXTURE_FORMAT
    assert metadata["source_capture_id"] == capture.capture_id
    assert metadata["database"]["classification_at_creation"] == "legacy"
    assert metadata["database"]["integrity_at_creation"] == "ok"


def test_fixture_is_disposable_and_does_not_modify_capture(tmp_path):
    capture = _capture(tmp_path)
    captured_before = capture.database_path.read_bytes()

    fixture = restore_fixture(capture.path, tmp_path / "fixtures")
    with sqlite3.connect(fixture.database_path) as connection:
        connection.execute("INSERT INTO core_rfid(rfid) VALUES ('fixture-only')")

    assert capture.database_path.read_bytes() == captured_before
    with sqlite3.connect(capture.database_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM core_rfid").fetchone()[0]
    assert count == 1


def test_restore_rejects_tampered_capture(tmp_path):
    capture = _capture(tmp_path)
    with capture.database_path.open("ab") as handle:
        handle.write(b"tamper")

    with pytest.raises(ValueError, match="checksum"):
        restore_fixture(capture.path, tmp_path / "fixtures")


def test_restore_never_overwrites_existing_fixture(tmp_path):
    capture = _capture(tmp_path)
    when = datetime(2026, 9, 25, 14, 5, tzinfo=timezone.utc)
    restore_fixture(capture.path, tmp_path / "fixtures", now=when)

    with pytest.raises(ValueError, match="already exists"):
        restore_fixture(capture.path, tmp_path / "fixtures", now=when)

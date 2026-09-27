from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from apps.ocpp.simulator.sources import resolve_replay_database
from arthexis.reconciliation.capture import capture_legacy_installation


def _v2_database(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE base_schemageneration "
            "(id INTEGER PRIMARY KEY, generation INTEGER)"
        )
        connection.execute(
            "INSERT INTO base_schemageneration(generation) VALUES (2)"
        )
    return path


def test_resolves_bare_current_generation_database(tmp_path) -> None:
    database = _v2_database(tmp_path / "current.sqlite3")

    resolved = resolve_replay_database(database)

    assert resolved.database == database.resolve()
    assert resolved.kind == "database"
    assert resolved.capture_id is None


def test_resolves_manifest_package_with_current_generation_database(tmp_path) -> None:
    package = tmp_path / "package"
    (package / "database").mkdir(parents=True)
    database = _v2_database(package / "database" / "current.sqlite3")
    (package / "manifest.json").write_text(
        json.dumps(
            {
                "format": "arthexis-replay-package-v1",
                "capture_id": "capture-123",
                "database": {"path": "database/current.sqlite3"},
            }
        ),
        encoding="utf-8",
    )

    resolved = resolve_replay_database(package)

    assert resolved.database == database.resolve()
    assert resolved.kind == "package"
    assert resolved.capture_id == "capture-123"


def test_verified_legacy_capture_requires_reconciliation(tmp_path) -> None:
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
    capture = capture_legacy_installation(
        source,
        tmp_path / "captures",
        now=datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
    )

    with pytest.raises(ValueError, match="reconcile the capture"):
        resolve_replay_database(capture.path)

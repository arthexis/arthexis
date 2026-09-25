"""Tests for cross-major reconciliation from disposable fixtures."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from arthexis.reconciliation.capture import capture_legacy_installation
from arthexis.reconciliation.fixture import restore_fixture
from arthexis.reconciliation.source import classify_database
from arthexis.reconciliation.workspace import verify_fixture_source

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fixture(tmp_path: Path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    database = legacy / "db.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE django_migrations "
            "(id INTEGER PRIMARY KEY, app TEXT, name TEXT, applied TEXT)"
        )
        connection.execute(
            "INSERT INTO django_migrations(app, name, applied) "
            "VALUES ('core', '0001_initial', '2024-01-01')"
        )
        connection.execute(
            "CREATE TABLE core_rfid "
            "(id INTEGER PRIMARY KEY, rfid TEXT, active INTEGER)"
        )
        connection.execute(
            "INSERT INTO core_rfid(rfid, active) VALUES ('legacy-card', 1)"
        )

    capture = capture_legacy_installation(
        legacy,
        tmp_path / "captures",
        now=datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc),
    )
    return restore_fixture(
        capture.path,
        tmp_path / "fixtures",
        now=datetime(2026, 9, 25, 14, 5, tzinfo=timezone.utc),
    )


def _run_reconciliation(fixture: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["ARTHEXIS_DATA_DIR"] = str(tmp_path / "current-data")
    environment.pop("ARTHEXIS_DATABASE_PATH", None)
    return subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "reconcile.py"),
            "reconcile-fixture",
            str(fixture),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_reconcile_fixture_creates_fresh_v2_output_without_mutating_source(tmp_path):
    fixture = _fixture(tmp_path)
    source_sha = _sha256(fixture.database_path)

    completed = _run_reconciliation(fixture.path, tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert _sha256(fixture.database_path) == source_sha

    destination = fixture.path / "reconciled.sqlite3"
    assert classify_database(destination) == "v2"
    with sqlite3.connect(destination) as connection:
        generation = connection.execute(
            "SELECT generation FROM base_schemageneration"
        ).fetchone()[0]
        cards = connection.execute(
            "SELECT COUNT(*) FROM cards_cardcredential"
        ).fetchone()[0]
    assert generation == 2
    assert cards == 1

    receipt = json.loads(
        (fixture.path / "reconciliation.json").read_text(encoding="utf-8")
    )
    assert receipt["status"] == "success"
    assert receipt["destination_database"]["classification"] == "v2"
    assert receipt["reconciliation"]["imported"]["card_credentials"] == 1


def test_reconcile_fixture_refuses_to_overwrite_output(tmp_path):
    fixture = _fixture(tmp_path)
    first = _run_reconciliation(fixture.path, tmp_path)
    assert first.returncode == 0, first.stderr

    second = _run_reconciliation(fixture.path, tmp_path)

    assert second.returncode != 0
    assert "already exists" in second.stderr


def test_fixture_verification_rejects_modified_source(tmp_path):
    fixture = _fixture(tmp_path)
    with sqlite3.connect(fixture.database_path) as connection:
        connection.execute(
            "INSERT INTO core_rfid(rfid, active) VALUES ('unexpected', 1)"
        )

    with pytest.raises(ValueError, match="changed after restore"):
        verify_fixture_source(fixture.path)

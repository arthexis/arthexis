"""Tests for Phase 4 migration verification and GO/NO-GO reporting."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from arthexis.reconciliation.capture import capture_legacy_installation
from arthexis.reconciliation.fixture import restore_fixture

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _fixture(tmp_path: Path) -> Path:
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
        now=datetime(2026, 9, 25, 15, 30, tzinfo=timezone.utc),
    )
    fixture = restore_fixture(
        capture.path,
        tmp_path / "fixtures",
        now=datetime(2026, 9, 25, 15, 31, tzinfo=timezone.utc),
    )
    return fixture.path


def _run(command: str, fixture: Path, tmp_path: Path):
    environment = os.environ.copy()
    environment["ARTHEXIS_DATA_DIR"] = str(tmp_path / "current-data")
    environment.pop("ARTHEXIS_DATABASE_PATH", None)
    return subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "reconcile.py"),
            command,
            str(fixture),
            "--nice",
            "0",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_verify_migration_emits_go_report_for_healthy_reconciliation(tmp_path):
    fixture = _fixture(tmp_path)
    reconciliation = _run("reconcile-fixture", fixture, tmp_path)
    assert reconciliation.returncode == 0, reconciliation.stderr

    verification = _run("verify-migration", fixture, tmp_path)

    assert verification.returncode == 0, verification.stderr
    report = json.loads((fixture / "migration-report.json").read_text(encoding="utf-8"))
    assert report["decision"] == "GO"
    assert report["failures"] == []
    assert "Decision: GO" in (fixture / "migration-report.txt").read_text(
        encoding="utf-8"
    )


def test_verify_migration_emits_no_go_for_retained_dependency_loss(tmp_path):
    fixture = _fixture(tmp_path)
    reconciliation = _run("reconcile-fixture", fixture, tmp_path)
    assert reconciliation.returncode == 0, reconciliation.stderr

    receipt_path = fixture / "reconciliation.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["reconciliation"]["skipped"]["ledger_entries"] = (
        "account dependency absent"
    )
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    verification = _run("verify-migration", fixture, tmp_path)

    assert verification.returncode == 2
    report = json.loads((fixture / "migration-report.json").read_text(encoding="utf-8"))
    assert report["decision"] == "NO-GO"
    assert any("ledger_entries" in failure for failure in report["failures"])

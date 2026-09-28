"""Tests for Phase 4 migration verification and GO/NO-GO reporting."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from arthexis.reconciliation.capture import capture_legacy_installation
from arthexis.reconciliation.fixture import restore_fixture
from arthexis.reconciliation.verification import verify_reconciliation

PROJECT_ROOT = Path(__file__).resolve().parents[3]


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


def _reconcile(fixture: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["ARTHEXIS_DATA_DIR"] = str(tmp_path / "current-data")
    environment.pop("ARTHEXIS_DATABASE_PATH", None)
    return subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "reconcile.py"),
            "reconcile-fixture",
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


@pytest.fixture(scope="module")
def reconciled_baseline(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp_path = tmp_path_factory.mktemp("reconciliation-verification")
    fixture = _fixture(tmp_path)
    reconciliation = _reconcile(fixture, tmp_path)
    assert reconciliation.returncode == 0, reconciliation.stderr
    return fixture


def _copy_baseline(reconciled_baseline: Path, tmp_path: Path) -> Path:
    fixture = tmp_path / "fixture"
    shutil.copytree(reconciled_baseline, fixture)
    return fixture


@pytest.mark.reconciliation_e2e
def test_verify_migration_emits_go_report_for_healthy_reconciliation(
    reconciled_baseline,
    tmp_path,
):
    fixture = _copy_baseline(reconciled_baseline, tmp_path)

    result = verify_reconciliation(fixture)

    assert result.decision == "GO"
    assert result.report["failures"] == []
    assert "Decision: GO" in result.text_path.read_text(encoding="utf-8")


@pytest.mark.reconciliation_e2e
def test_verify_migration_accepts_source_dependency_gap_with_warning(
    reconciled_baseline,
    tmp_path,
):
    fixture = _copy_baseline(reconciled_baseline, tmp_path)
    receipt_path = fixture / "reconciliation.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["reconciliation"]["skipped"]["ledger_entries"] = (
        "account dependency absent"
    )
    receipt["reconciliation"].setdefault("historical_gaps", []).append(
        {
            "resource": "ledger_entries",
            "classification": "source-incomplete",
            "reason": "account dependency absent",
            "evidence": (
                "Legacy ledger row references an account absent from the captured source."
            ),
        }
    )
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    result = verify_reconciliation(fixture)

    assert result.decision == "GO"
    assert result.report["failures"] == []
    assert result.report["historical_gaps"][0]["classification"] == "source-incomplete"
    assert any("ledger_entries" in warning for warning in result.report["warnings"])


@pytest.mark.reconciliation_e2e
def test_verify_migration_rejects_unclassified_retained_dependency_loss(
    reconciled_baseline,
    tmp_path,
):
    fixture = _copy_baseline(reconciled_baseline, tmp_path)
    receipt_path = fixture / "reconciliation.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["reconciliation"]["skipped"]["ledger_entries"] = (
        "account dependency absent"
    )
    receipt["reconciliation"]["historical_gaps"] = []
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    result = verify_reconciliation(fixture)

    assert result.decision == "NO-GO"
    assert any("ledger_entries" in failure for failure in result.report["failures"])


@pytest.mark.reconciliation_e2e
def test_verify_migration_rejects_gap_without_evidence(
    reconciled_baseline,
    tmp_path,
):
    fixture = _copy_baseline(reconciled_baseline, tmp_path)
    receipt_path = fixture / "reconciliation.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["reconciliation"]["skipped"]["ledger_entries"] = (
        "account dependency absent"
    )
    receipt["reconciliation"]["historical_gaps"] = [
        {
            "resource": "ledger_entries",
            "classification": "source-incomplete",
            "reason": "account dependency absent",
            "evidence": "",
        }
    ]
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    result = verify_reconciliation(fixture)

    assert result.decision == "NO-GO"
    assert any("lacks source evidence" in failure for failure in result.report["failures"])


@pytest.mark.reconciliation_e2e
def test_verify_migration_accepts_legacy_v0_gap_with_warning(
    reconciled_baseline,
    tmp_path,
):
    fixture = _copy_baseline(reconciled_baseline, tmp_path)
    receipt_path = fixture / "reconciliation.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["reconciliation"]["skipped"]["legacy_sessions"] = (
        "version 0 did not persist complete session history"
    )
    receipt["reconciliation"].setdefault("historical_gaps", []).append(
        {
            "resource": "legacy_sessions",
            "classification": "legacy-v0-gap",
            "reason": "version 0 did not persist complete session history",
            "evidence": "Captured source schema has no durable session-history table.",
        }
    )
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    result = verify_reconciliation(fixture)

    assert result.decision == "GO"
    assert any("legacy-v0-gap" in warning for warning in result.report["warnings"])

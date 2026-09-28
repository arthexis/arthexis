"""Tests for the end-to-end legacy migration rehearsal command."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _legacy_installation(root: Path) -> Path:
    root.mkdir()
    database = root / "db.sqlite3"
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
    (root / "VERSION").write_text("0.9.0\n", encoding="utf-8")
    return database


@pytest.mark.reconciliation_e2e
def test_rehearse_runs_from_live_source_through_go_report_without_mutating_source(
    tmp_path,
):
    legacy = tmp_path / "legacy"
    source_database = _legacy_installation(legacy)
    source_sha = _sha256(source_database)
    output = tmp_path / "rehearsal"

    environment = os.environ.copy()
    environment["ARTHEXIS_DATA_DIR"] = str(tmp_path / "current-data")
    environment.pop("ARTHEXIS_DATABASE_PATH", None)

    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "reconcile.py"),
            "rehearse",
            str(legacy),
            "--output",
            str(output),
            "--nice",
            "0",
            "--batch-size",
            "1",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert _sha256(source_database) == source_sha

    result = json.loads(completed.stdout)
    assert result["decision"] == "GO"
    assert result["source"] == str(legacy.resolve())
    assert result["resource_safety"]["decision"] == "GO"
    assert Path(result["resource_report"]).is_file()
    bundle = Path(result["go_bundle"])
    assert bundle.is_dir()
    assert (bundle / "FINALIZED").is_file()
    assert (bundle / "manifest.json").is_file()
    assert (bundle / "checksums.sha256").is_file()
    assert (bundle / "database" / "reconciled.sqlite3").is_file()

    capture = Path(result["capture"]["path"])
    fixture = Path(result["fixture"]["path"])
    destination = Path(result["destination_database"])
    assert capture.is_dir()
    assert (capture / "FINALIZED").is_file()
    assert fixture.is_dir()
    assert destination.is_file()
    assert Path(result["json_report"]).is_file()
    assert Path(result["text_report"]).is_file()

    report = json.loads(Path(result["json_report"]).read_text(encoding="utf-8"))
    assert report["decision"] == "GO"
    assert report["source_capture_id"] == result["capture"]["capture_id"]


@pytest.mark.reconciliation_e2e
def test_rehearse_stops_with_no_go_when_resource_limit_is_exceeded(tmp_path):
    legacy = tmp_path / "legacy"
    source_database = _legacy_installation(legacy)
    source_sha = _sha256(source_database)
    output = tmp_path / "rehearsal"

    environment = os.environ.copy()
    environment["ARTHEXIS_DATA_DIR"] = str(tmp_path / "current-data")
    environment.pop("ARTHEXIS_DATABASE_PATH", None)

    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "reconcile.py"),
            "rehearse",
            str(legacy),
            "--output",
            str(output),
            "--nice",
            "0",
            "--max-workspace-mib",
            "0.001",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2, completed.stderr
    assert _sha256(source_database) == source_sha

    result = json.loads(completed.stdout)
    assert result["decision"] == "NO-GO"
    assert result["reason"] == "resource-limit"
    assert result["resource_safety"]["decision"] == "NO-GO"
    assert any(
        violation["resource"] == "workspace_mib"
        for violation in result["resource_safety"]["violations"]
    )
    assert Path(result["resource_report"]).is_file()
    assert not Path(result["fixture"]["path"], "migration-report.json").exists()


def test_rehearse_refuses_capture_when_free_disk_preflight_fails(tmp_path):
    legacy = tmp_path / "legacy"
    source_database = _legacy_installation(legacy)
    source_sha = _sha256(source_database)
    output = tmp_path / "rehearsal"

    environment = os.environ.copy()
    environment["ARTHEXIS_DATA_DIR"] = str(tmp_path / "current-data")
    environment.pop("ARTHEXIS_DATABASE_PATH", None)

    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "reconcile.py"),
            "rehearse",
            str(legacy),
            "--output",
            str(output),
            "--min-free-disk-mib",
            "1000000000",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert _sha256(source_database) == source_sha

    result = json.loads(completed.stdout)
    assert result["decision"] == "NO-GO"
    assert result["reason"] == "resource-limit"
    assert result["resource_preflight"]["violations"]
    assert not (output / "captures").exists()


@pytest.mark.reconciliation_e2e
def test_go_bundle_is_immutable_and_refuses_overwrite(tmp_path):
    from arthexis.reconciliation.rehearsal import create_go_bundle

    legacy = tmp_path / "legacy"
    _legacy_installation(legacy)
    output = tmp_path / "rehearsal"

    environment = os.environ.copy()
    environment["ARTHEXIS_DATA_DIR"] = str(tmp_path / "current-data")
    environment.pop("ARTHEXIS_DATABASE_PATH", None)

    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "reconcile.py"),
            "rehearse",
            str(legacy),
            "--output",
            str(output),
            "--nice",
            "0",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)

    with pytest.raises(ValueError, match="already exists"):
        create_go_bundle(
            output,
            capture_path=Path(result["capture"]["path"]),
            fixture_path=Path(result["fixture"]["path"]),
            migration_report=Path(result["json_report"]),
            migration_text_report=Path(result["text_report"]),
            resource_report=Path(result["resource_report"]),
        )


@pytest.mark.reconciliation_e2e
def test_cutover_rehearsal_requires_no_missed_writes_proof(tmp_path):
    legacy = tmp_path / "legacy"
    _legacy_installation(legacy)
    output = tmp_path / "rehearsal"

    environment = os.environ.copy()
    environment["ARTHEXIS_DATA_DIR"] = str(tmp_path / "current-data")
    environment.pop("ARTHEXIS_DATABASE_PATH", None)

    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "reconcile.py"),
            "rehearse",
            str(legacy),
            "--output",
            str(output),
            "--nice",
            "0",
            "--cutover",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["decision"] == "GO"
    assert result["cutover"]["mode"] == "final-cutover"
    assert result["cutover"]["no_missed_writes"] is True

    bundle = Path(result["go_bundle"])
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["cutover"]["mode"] == "final-cutover"
    assert manifest["cutover"]["no_missed_writes"] is True
    assert (bundle / "migration" / "cutover-proof.json").is_file()


@pytest.mark.reconciliation_e2e
def test_cutover_rehearsal_returns_no_go_if_live_source_changes(tmp_path, monkeypatch):
    from arthexis.reconciliation import rehearsal

    legacy = tmp_path / "legacy"
    source_database = _legacy_installation(legacy)
    output = tmp_path / "rehearsal"

    original = rehearsal.verify_cutover_source_unchanged

    def mutate_then_verify(*args, **kwargs):
        with sqlite3.connect(source_database) as connection:
            connection.execute(
                "INSERT INTO core_rfid(rfid, active) VALUES ('late-write', 1)"
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(
        rehearsal,
        "verify_cutover_source_unchanged",
        mutate_then_verify,
    )

    from scripts import reconcile

    arguments = reconcile._parser().parse_args(
        [
            "rehearse",
            str(legacy),
            "--output",
            str(output),
            "--nice",
            "0",
            "--cutover",
        ]
    )
    result_code = reconcile._rehearse(arguments)

    assert result_code == 2
    proof = json.loads((output / "cutover-proof.json").read_text(encoding="utf-8"))
    assert proof["decision"] == "NO-GO"
    assert proof["no_missed_writes"] is False
    assert not any((output / "bundles").iterdir()) if (output / "bundles").exists() else True


@pytest.mark.reconciliation_e2e
def test_cutover_rehearsal_requires_unchanged_live_source(tmp_path):
    legacy = tmp_path / "legacy"
    source_database = _legacy_installation(legacy)
    output = tmp_path / "rehearsal"

    environment = os.environ.copy()
    environment["ARTHEXIS_DATA_DIR"] = str(tmp_path / "current-data")
    environment.pop("ARTHEXIS_DATABASE_PATH", None)

    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "reconcile.py"),
            "rehearse",
            str(legacy),
            "--output",
            str(output),
            "--nice",
            "0",
            "--cutover",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["decision"] == "GO"
    assert result["cutover"]["decision"] == "GO"
    assert result["cutover"]["no_missed_writes"] is True
    bundle = Path(result["go_bundle"])
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["cutover"]["mode"] == "final-cutover"
    assert manifest["cutover"]["no_missed_writes_proven"] is True
    assert (bundle / "migration" / "cutover-proof.json").is_file()
    assert _sha256(source_database) == result["capture"]["database_sha256"]


def test_cutover_proof_rejects_live_source_advanced_after_capture(tmp_path):
    from arthexis.reconciliation.capture import capture_legacy_installation
    from arthexis.reconciliation.rehearsal import verify_cutover_source_unchanged

    legacy = tmp_path / "legacy"
    source_database = _legacy_installation(legacy)
    capture = capture_legacy_installation(legacy, tmp_path / "captures")

    with sqlite3.connect(source_database) as connection:
        connection.execute(
            "INSERT INTO core_rfid(rfid, active) VALUES ('post-capture-card', 1)"
        )

    proof = verify_cutover_source_unchanged(
        legacy,
        tmp_path / "rehearsal",
        expected_database_sha256=_sha256(capture.database_path),
    )

    assert proof["decision"] == "NO-GO"
    assert proof["no_missed_writes"] is False
    assert proof["reason"] == "legacy-source-advanced-after-capture"
    assert Path(proof["proof_path"]).is_file()

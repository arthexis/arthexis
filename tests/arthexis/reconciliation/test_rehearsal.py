"""Tests for the end-to-end legacy migration rehearsal command."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
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


def _run_rehearsal(
    legacy: Path,
    output: Path,
    *extra: str,
    data_dir: Path,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["ARTHEXIS_DATA_DIR"] = str(data_dir)
    environment.pop("ARTHEXIS_DATABASE_PATH", None)
    return subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "reconcile.py"),
            "rehearse",
            str(legacy),
            "--output",
            str(output),
            "--nice",
            "0",
            *extra,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _json_result(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(completed.stdout)


def _baseline_bundle_inputs(
    reconciled_e2e_baseline: dict[str, object],
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, Path]:
    from arthexis.reconciliation.verification import verify_reconciliation

    capture = Path(reconciled_e2e_baseline["capture_path"])
    fixture = tmp_path / "fixture"
    shutil.copytree(Path(reconciled_e2e_baseline["fixture_path"]), fixture)
    verification = verify_reconciliation(fixture)
    resource_report = tmp_path / "resource-report.json"
    resource_report.write_text(
        json.dumps({"decision": "GO", "policy": {}, "usage": {}}, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return (
        capture,
        fixture,
        verification.json_path,
        verification.text_path,
        resource_report,
    )


def _assert_go_bundle(result: dict[str, object]) -> Path:
    bundle = Path(str(result["go_bundle"]))
    assert bundle.is_dir()
    assert (bundle / "FINALIZED").is_file()
    assert (bundle / "manifest.json").is_file()
    assert (bundle / "checksums.sha256").is_file()
    assert (bundle / "database" / "reconciled.sqlite3").is_file()
    return bundle


@pytest.mark.reconciliation_e2e
def test_rehearse_runs_from_live_source_through_go_report_without_mutating_source(
    tmp_path,
):
    legacy = tmp_path / "legacy"
    source_database = _legacy_installation(legacy)
    source_sha = _sha256(source_database)
    output = tmp_path / "rehearsal"

    completed = _run_rehearsal(
        legacy,
        output,
        "--batch-size",
        "1",
        data_dir=tmp_path / "current-data",
    )

    assert completed.returncode == 0, completed.stderr
    assert _sha256(source_database) == source_sha

    result = _json_result(completed)
    assert result["decision"] == "GO"
    assert result["source"] == str(legacy.resolve())
    assert result["resource_safety"]["decision"] == "GO"
    assert Path(result["resource_report"]).is_file()
    _assert_go_bundle(result)

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

    completed = _run_rehearsal(
        legacy,
        output,
        "--max-workspace-mib",
        "0.001",
        data_dir=tmp_path / "current-data",
    )

    assert completed.returncode == 2, completed.stderr
    assert _sha256(source_database) == source_sha

    result = _json_result(completed)
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

    completed = _run_rehearsal(
        legacy,
        output,
        "--min-free-disk-mib",
        "1000000000",
        data_dir=tmp_path / "current-data",
    )

    assert completed.returncode == 2
    assert _sha256(source_database) == source_sha

    result = _json_result(completed)
    assert result["decision"] == "NO-GO"
    assert result["reason"] == "resource-limit"
    assert result["resource_preflight"]["violations"]
    assert not (output / "captures").exists()


@pytest.mark.reconciliation_e2e
def test_go_bundle_is_immutable_and_refuses_overwrite(
    reconciled_e2e_baseline,
    tmp_path,
):
    from arthexis.reconciliation.rehearsal import create_go_bundle

    capture, fixture, json_report, text_report, resource_report = (
        _baseline_bundle_inputs(reconciled_e2e_baseline, tmp_path)
    )
    output = tmp_path / "rehearsal"
    create_go_bundle(
        output,
        capture_path=capture,
        fixture_path=fixture,
        migration_report=json_report,
        migration_text_report=text_report,
        resource_report=resource_report,
    )

    with pytest.raises(ValueError, match="already exists"):
        create_go_bundle(
            output,
            capture_path=capture,
            fixture_path=fixture,
            migration_report=json_report,
            migration_text_report=text_report,
            resource_report=resource_report,
        )


@pytest.mark.reconciliation_e2e
def test_cutover_rehearsal_requires_no_missed_writes_proof(tmp_path):
    legacy = tmp_path / "legacy"
    _legacy_installation(legacy)
    output = tmp_path / "rehearsal"

    completed = _run_rehearsal(
        legacy,
        output,
        "--cutover",
        data_dir=tmp_path / "current-data",
    )

    assert completed.returncode == 0, completed.stderr
    result = _json_result(completed)
    assert result["decision"] == "GO"
    assert result["cutover"]["decision"] == "GO"
    assert result["cutover"]["no_missed_writes"] is True

    bundle = _assert_go_bundle(result)
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["cutover"]["mode"] == "final-cutover"
    assert manifest["cutover"]["no_missed_writes_proven"] is True
    assert (bundle / "migration" / "cutover-proof.json").is_file()


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
    changed_sha = _sha256(source_database)

    proof = verify_cutover_source_unchanged(
        legacy,
        tmp_path / "rehearsal",
        expected_database_sha256=_sha256(capture.database_path),
    )

    assert proof["decision"] == "NO-GO"
    assert proof["no_missed_writes"] is False
    assert proof["reason"] == "legacy-source-advanced-after-capture"
    assert Path(proof["proof_path"]).is_file()
    assert _sha256(source_database) == changed_sha


@pytest.mark.reconciliation_e2e
def test_rehearse_can_be_rerun_without_overwriting_prior_evidence(tmp_path):
    legacy = tmp_path / "legacy"
    source_database = _legacy_installation(legacy)
    source_sha = _sha256(source_database)
    output = tmp_path / "rehearsal"

    first = _run_rehearsal(
        legacy,
        output,
        data_dir=tmp_path / "current-data",
    )
    second = _run_rehearsal(
        legacy,
        output,
        data_dir=tmp_path / "current-data",
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert _sha256(source_database) == source_sha

    first_result = _json_result(first)
    second_result = _json_result(second)
    assert first_result["capture"]["capture_id"] != second_result["capture"]["capture_id"]
    assert first_result["go_bundle"] != second_result["go_bundle"]
    assert Path(first_result["go_bundle"]).is_dir()
    assert Path(second_result["go_bundle"]).is_dir()


@pytest.mark.reconciliation_e2e
def test_go_bundle_refuses_no_go_verification_and_leaves_no_final_artifact(
    reconciled_e2e_baseline,
    tmp_path,
):
    from arthexis.reconciliation.rehearsal import create_go_bundle

    capture, fixture, json_report, text_report, resource_report = (
        _baseline_bundle_inputs(reconciled_e2e_baseline, tmp_path)
    )
    no_go_report = tmp_path / "no-go-report.json"
    report = json.loads(json_report.read_text(encoding="utf-8"))
    report["decision"] = "NO-GO"
    no_go_report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    alternate_root = tmp_path / "alternate-bundle-root"
    with pytest.raises(ValueError, match="successful migration verification"):
        create_go_bundle(
            alternate_root,
            capture_path=capture,
            fixture_path=fixture,
            migration_report=no_go_report,
            migration_text_report=text_report,
            resource_report=resource_report,
        )

    bundles = alternate_root / "bundles"
    assert not bundles.exists() or not any(bundles.iterdir())



def test_rehearse_help_links_operator_migration_document():
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "reconcile.py"),
            "rehearse",
            "--help",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "Legacy satellite migration rehearsal" in completed.stdout
    assert "docs/legacy-satellite-migration.md" in completed.stdout
    assert (
        "https://github.com/arthexis/arthexis/blob/main/"
        "docs/legacy-satellite-migration.md"
    ) in completed.stdout
    assert "--cutover" in completed.stdout

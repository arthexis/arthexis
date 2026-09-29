"""Tests for Phase 4 migration verification and GO/NO-GO reporting."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from arthexis.reconciliation.verification import verify_reconciliation


def _copy_baseline(reconciled_e2e_baseline: dict[str, object], tmp_path: Path) -> Path:
    fixture = tmp_path / "fixture"
    shutil.copytree(Path(reconciled_e2e_baseline["fixture_path"]), fixture)
    return fixture


@pytest.mark.reconciliation_e2e
def test_verify_migration_emits_go_report_for_healthy_reconciliation(
    reconciled_e2e_baseline,
    tmp_path,
):
    fixture = _copy_baseline(reconciled_e2e_baseline, tmp_path)

    result = verify_reconciliation(fixture)

    assert result.decision == "GO"
    assert result.report["failures"] == []
    assert "Decision: GO" in result.text_path.read_text(encoding="utf-8")


@pytest.mark.reconciliation_e2e
def test_verify_migration_accepts_source_dependency_gap_with_warning(
    reconciled_e2e_baseline,
    tmp_path,
):
    fixture = _copy_baseline(reconciled_e2e_baseline, tmp_path)
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
    reconciled_e2e_baseline,
    tmp_path,
):
    fixture = _copy_baseline(reconciled_e2e_baseline, tmp_path)
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
    reconciled_e2e_baseline,
    tmp_path,
):
    fixture = _copy_baseline(reconciled_e2e_baseline, tmp_path)
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
    reconciled_e2e_baseline,
    tmp_path,
):
    fixture = _copy_baseline(reconciled_e2e_baseline, tmp_path)
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

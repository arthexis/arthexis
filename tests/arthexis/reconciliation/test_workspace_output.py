from __future__ import annotations

import io
import warnings
from pathlib import Path

from arthexis.reconciliation.importer import ReconciliationReport
from arthexis.reconciliation.workspace import (
    _print_progress_summary,
    _warning_key,
    _write_warning_receipt,
)


def test_warning_key_deduplicates_naive_datetime_values() -> None:
    first = _warning_key(
        "DateTimeField OcppTransaction.started_at received a naive datetime "
        "(2026-05-05 12:44:39.965742) while time zone support is active."
    )
    second = _warning_key(
        "DateTimeField OcppTransaction.started_at received a naive datetime "
        "(2026-05-05 12:45:22.783133) while time zone support is active."
    )

    assert first == second


def test_warning_receipt_groups_repeated_warnings(tmp_path: Path) -> None:
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        warnings.warn(
            "DateTimeField OcppTransaction.started_at received a naive datetime "
            "(2026-05-05 12:44:39) while time zone support is active.",
            RuntimeWarning,
        )
        warnings.warn(
            "DateTimeField OcppTransaction.started_at received a naive datetime "
            "(2026-05-05 12:44:40) while time zone support is active.",
            RuntimeWarning,
        )

    path, counts = _write_warning_receipt(tmp_path, captured)

    assert path == tmp_path / "reconciliation-warnings.json"
    assert path.is_file()
    assert list(counts.values()) == [2]


def test_progress_summary_is_compact_and_reports_details(tmp_path: Path) -> None:
    report = ReconciliationReport(
        source="legacy.sqlite3",
        source_sha256="sha",
        source_size=1,
        dry_run=False,
        imported={"ocpp_transactions": 3, "chargers": 1},
    )
    stream = io.StringIO()
    details = tmp_path / "reconciliation-warnings.json"

    _print_progress_summary(
        stream,
        report,
        {"DateTimeField warning": 7},
        details,
    )

    output = stream.getvalue()
    assert "Transactions:\n...\n" in output
    assert "4 imported · 3 transactions · 7 warnings" in output
    assert "7 × DateTimeField warning" in output
    assert f"Details: {details}" in output

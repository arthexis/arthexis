"""Regression tests for PR marker validation across changed test files."""

from pathlib import Path

import pytest

from scripts.check_test_pr_markers import validate_test_file


pytestmark = [
    pytest.mark.regression,
    pytest.mark.pr("PR-5652", "2026-02-26T00:00:00Z"),
]


def test_validate_accepts_pr_marker_with_iso_datetime(tmp_path: Path) -> None:
    """Validation should pass for a valid marker reference and ISO timestamp."""

    target = tmp_path / "test_valid.py"
    target.write_text(
        "import pytest\n\n"
        "pytestmark = pytest.mark.pr('PR-5652', '2026-02-26T13:45:00Z')\n\n"
        "def test_ok():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    assert validate_test_file(target, expected_pr="PR-5652") == []


def test_validate_rejects_missing_pr_marker(tmp_path: Path) -> None:
    """Validation should fail when no pytest.mark.pr marker exists."""

    target = tmp_path / "test_missing.py"
    target.write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    failures = validate_test_file(target)
    assert len(failures) == 1
    assert "missing pytest PR marker" in failures[0].message


def test_validate_rejects_non_iso_second_argument(tmp_path: Path) -> None:
    """Validation should fail when the timestamp is not ISO-8601 formatted."""

    target = tmp_path / "test_bad_date.py"
    target.write_text(
        "import pytest\n\n"
        "pytestmark = pytest.mark.pr('PR-5652', 'yesterday')\n\n"
        "def test_ok():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    failures = validate_test_file(target)
    assert len(failures) == 1
    assert "ISO-8601" in failures[0].message


def test_validate_rejects_missing_expected_pr_reference(tmp_path: Path) -> None:
    """Validation should fail when changed tests do not reference the current PR."""

    target = tmp_path / "test_other_pr.py"
    target.write_text(
        "import pytest\n\n"
        "pytestmark = pytest.mark.pr('PR-1000', '2026-02-26T13:45:00Z')\n\n"
        "def test_ok():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    failures = validate_test_file(target, expected_pr="PR-5652")
    assert len(failures) == 1
    assert "must include reference PR-5652" in failures[0].message

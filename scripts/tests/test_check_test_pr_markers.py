from pathlib import Path

import pytest

from scripts.check_test_pr_markers import validate_test_file


pytestmark = pytest.mark.pr("PR-5652", "2026-02-26T00:00:00Z")


def test_validate_accepts_pr_marker_with_iso_datetime(tmp_path: Path) -> None:
    target = tmp_path / "test_valid.py"
    target.write_text(
        "import pytest\n\n"
        "pytestmark = pytest.mark.pr('PR-5652', '2026-02-26T13:45:00Z')\n\n"
        "def test_ok():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    assert validate_test_file(target) == []


def test_validate_rejects_missing_pr_marker(tmp_path: Path) -> None:
    target = tmp_path / "test_missing.py"
    target.write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    failures = validate_test_file(target)
    assert len(failures) == 1
    assert "missing pytest PR marker" in failures[0].message


def test_validate_rejects_non_iso_second_argument(tmp_path: Path) -> None:
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

"""Tests for staged test PR-origin marker validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_test_pr_markers import validate_test_file

pytestmark = pytest.mark.pr_origin(6260)


def test_validate_test_file_accepts_pr_origin_marker(tmp_path: Path) -> None:
    """Files with a ``pr_origin`` marker should pass validation."""

    path = tmp_path / "test_sample.py"
    path.write_text(
        "import pytest\n\n"
        "pytestmark = pytest.mark.pr_origin(6200)\n\n"
        "def test_example():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    assert validate_test_file(path) == []


def test_validate_test_file_rejects_missing_pr_origin_marker(tmp_path: Path) -> None:
    """Files without ``pr_origin`` marker should fail validation."""

    path = tmp_path / "test_sample.py"
    path.write_text(
        "def test_example():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    failures = validate_test_file(path)

    assert len(failures) == 1
    assert "missing pytest PR marker" in failures[0].message


def test_validate_test_file_requires_expected_pr_reference(tmp_path: Path) -> None:
    """Expected PR references should be enforced when provided."""

    path = tmp_path / "test_sample.py"
    path.write_text(
        "import pytest\n\n"
        "@pytest.mark.pr_origin(6250)\n"
        "def test_example():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    failures = validate_test_file(path, expected_pr="6251")

    assert len(failures) == 1
    assert "must include reference 6251" in failures[0].message

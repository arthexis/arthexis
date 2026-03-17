"""Tests for staged test PR-origin marker validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_test_pr_markers import main, validate_test_file

pytestmark = pytest.mark.pr_origin(6260)


def test_validate_test_file_accepts_pr_origin_marker(tmp_path: Path) -> None:
    """Verify files with a ``pr_origin`` marker pass validation.

    :param tmp_path: Temporary directory used to write the sample test file.
    :return: ``None``.
    """

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
    """Verify files without a ``pr_origin`` marker fail validation.

    :param tmp_path: Temporary directory used to write the sample test file.
    :return: ``None``.
    """

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
    """Verify expected PR references are enforced when provided.

    :param tmp_path: Temporary directory used to write the sample test file.
    :return: ``None``.
    """

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


def test_validate_test_file_rejects_empty_pr_origin_reference(tmp_path: Path) -> None:
    """Verify empty ``pr_origin`` markers are rejected.

    :param tmp_path: Temporary directory used to write the sample test file.
    :return: ``None``.
    """

    path = tmp_path / "test_sample.py"
    path.write_text(
        "import pytest\n\n"
        "@pytest.mark.pr_origin()\n"
        "def test_example():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    failures = validate_test_file(path)

    assert len(failures) == 1
    assert "must include a reference argument" in failures[0].message


def test_main_validates_explicit_paths_without_staged_diff(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Verify explicit CLI path mode validates files from working tree content.

    :param tmp_path: Temporary directory used to write sample test files.
    :param capsys: Pytest capture fixture for stderr assertions.
    :return: ``None``.
    """

    missing_marker = tmp_path / "test_missing_marker.py"
    missing_marker.write_text(
        "def test_missing_marker():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    passing_marker = tmp_path / "test_passing_marker.py"
    passing_marker.write_text(
        "import pytest\n\n"
        "pytestmark = pytest.mark.pr_origin(6264)\n\n"
        "def test_with_marker():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    missing_result = main([str(missing_marker)])
    missing_output = capsys.readouterr()
    passing_result = main([str(passing_marker)])

    assert missing_result == 1
    assert "missing pytest PR marker" in missing_output.err
    assert passing_result == 0

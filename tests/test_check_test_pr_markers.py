"""Tests for staged test PR-origin marker validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_test_pr_markers import (
    detect_current_pr_reference,
    main,
    rewrite_pr_origin_markers,
    validate_test_file,
)

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


@pytest.mark.pr_origin(6278)
def test_detect_current_pr_reference_supports_common_environment_variables() -> None:
    """Verify PR detection resolves values from common CI variables.

    :return: ``None``.
    """

    assert detect_current_pr_reference({"CURRENT_PR": "6275"}) == "6275"
    assert detect_current_pr_reference({"PR_NUMBER": "6275"}) == "6275"
    assert detect_current_pr_reference({"GITHUB_PR_NUMBER": "6275"}) == "6275"
    assert detect_current_pr_reference({"GITHUB_REF_NAME": "6275"}) == "6275"
    assert detect_current_pr_reference({"GITHUB_REF": "refs/pull/6275/merge"}) == "6275"


@pytest.mark.pr_origin(6278)
def test_rewrite_pr_origin_markers_updates_existing_references(tmp_path: Path) -> None:
    """Verify marker rewrite normalizes file markers to the expected PR.

    :param tmp_path: Temporary directory used to write a sample test file.
    :return: ``None``.
    """

    path = tmp_path / "test_sample.py"
    path.write_text(
        "import pytest\n\n"
        "pytestmark = pytest.mark.pr_origin(6301)\n\n"
        "@pytest.mark.pr_origin(6301)\n"
        "def test_example():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    changed = rewrite_pr_origin_markers(path, "6275")

    assert changed is True
    assert "pr_origin(6301)" not in path.read_text(encoding="utf-8")
    assert validate_test_file(path, expected_pr="6275") == []


@pytest.mark.pr_origin(6278)
def test_main_fix_rewrites_marker_before_validation(tmp_path: Path) -> None:
    """Verify ``--fix`` rewrites marker references before validation.

    :param tmp_path: Temporary directory used to write a sample test file.
    :return: ``None``.
    """

    path = tmp_path / "test_sample.py"
    path.write_text(
        "import pytest\n\n"
        "pytestmark = pytest.mark.pr_origin(\"6275\")\n\n"
        "def test_example():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    result = main(["--current-pr", "6275", "--fix", str(path)])

    assert result == 0
    rewritten = path.read_text(encoding="utf-8")
    assert 'pytest.mark.pr_origin("6275")' not in rewritten
    assert "pytest.mark.pr_origin(6275)" in rewritten


@pytest.mark.pr_origin(6278)
def test_rewrite_pr_origin_markers_only_rewrites_real_calls(tmp_path: Path) -> None:
    """Verify marker rewrite ignores textual lookalikes in comments and strings.

    :param tmp_path: Temporary directory used to write a sample test file.
    :return: ``None``.
    """

    path = tmp_path / "test_sample.py"
    path.write_text(
        "import pytest\n\n"
        "COMMENT = \"pytest.mark.pr_origin(1111)\"\n"
        "# pytest.mark.pr_origin(1111)\n"
        "pytestmark = pytest.mark.pr_origin(1111)\n\n"
        "@pytest.mark.pr_origin(1111 + 1)\n"
        "def test_example():\n"
        "    assert COMMENT\n",
        encoding="utf-8",
    )

    changed = rewrite_pr_origin_markers(path, "6278")

    assert changed is True
    rewritten = path.read_text(encoding="utf-8")
    assert 'COMMENT = "pytest.mark.pr_origin(1111)"' in rewritten
    assert '# pytest.mark.pr_origin(1111)' in rewritten
    assert rewritten.count("pytest.mark.pr_origin(6278)") == 2


@pytest.mark.pr_origin(6278)
def test_main_rejects_mixed_pr_markers(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Verify mixed marker references fail before validation/fix steps.

    :param tmp_path: Temporary directory used to write a sample test file.
    :param capsys: Pytest capture fixture for stderr assertions.
    :return: ``None``.
    """

    path = tmp_path / "test_mixed.py"
    path.write_text(
        "import pytest\n\n"
        "pytestmark = [pytest.mark.pr_origin(6278), pytest.mark.pr_origin(6200)]\n\n"
        "def test_example():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    result = main(["--current-pr", "6278", "--fix", str(path)])
    output = capsys.readouterr()

    assert result == 1
    assert "found mixed pr_origin references" in output.err


@pytest.mark.pr_origin(6278)
def test_main_rejects_non_numeric_current_pr(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify non-numeric ``--current-pr`` values fail fast with an error.

    :param capsys: Pytest capture fixture for stderr assertions.
    :return: ``None``.
    """

    result = main(["--current-pr", "not-a-number"])
    output = capsys.readouterr()

    assert result == 1
    assert "PR reference must be a number" in output.err


@pytest.mark.pr_origin(6278)
def test_main_fix_restages_rewritten_files_in_staged_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify staged ``--fix`` mode re-adds rewritten files to the git index.

    :param monkeypatch: Pytest fixture used to stub git-dependent helpers.
    :return: ``None``.
    """

    from scripts import check_test_pr_markers as module

    changed_file = module.ChangedFile(path=Path("tests/test_sample.py"))
    restaged_paths: list[Path] = []

    monkeypatch.setattr(module, "_staged_changed_files", lambda: [changed_file])
    monkeypatch.setattr(module, "_is_test_file", lambda _: True)
    monkeypatch.setattr(module, "_file_introduces_new_tests", lambda _: True)
    monkeypatch.setattr(module, "_collect_marker_references", lambda _: {"6275"})
    monkeypatch.setattr(module, "rewrite_pr_origin_markers", lambda *_: True)
    monkeypatch.setattr(module, "validate_test_file", lambda *_, **__: [])
    monkeypatch.setattr(module, "_restage_file", lambda path: restaged_paths.append(path))

    result = module.main(["--current-pr", "6275", "--fix"])

    assert result == 0
    assert restaged_paths == [changed_file.path]


@pytest.mark.pr_origin(6278)
def test_main_reports_rewrite_parse_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify rewrite failures are reported as validation errors instead of crashing.

    :param monkeypatch: Pytest fixture used to stub git-dependent helpers.
    :param capsys: Pytest capture fixture for stderr assertions.
    :return: ``None``.
    """

    from scripts import check_test_pr_markers as module

    changed_file = module.ChangedFile(path=Path("tests/test_sample.py"))

    monkeypatch.setattr(module, "_staged_changed_files", lambda: [changed_file])
    monkeypatch.setattr(module, "_is_test_file", lambda _: True)
    monkeypatch.setattr(module, "_file_introduces_new_tests", lambda _: True)
    monkeypatch.setattr(module, "_collect_marker_references", lambda _: {"6275"})
    monkeypatch.setattr(
        module,
        "rewrite_pr_origin_markers",
        lambda *_: (_ for _ in ()).throw(SyntaxError("bad syntax")),
    )

    result = module.main(["--current-pr", "6275", "--fix"])
    output = capsys.readouterr()

    assert result == 1
    assert "unable to parse file: bad syntax" in output.err

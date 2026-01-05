from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.release import release
from apps.release.domain import release_tasks


def _mock_git_status(monkeypatch: pytest.MonkeyPatch, output: str) -> None:
    def fake_run(cmd, capture_output=False, text=False, cwd=None):  # noqa: ANN001
        return SimpleNamespace(stdout=output, returncode=0)

    monkeypatch.setattr(release.subprocess, "run", fake_run)
    monkeypatch.setattr(release_tasks.subprocess, "run", fake_run)
    monkeypatch.setattr(release, "_is_git_repository", lambda base_dir=None: True)


def test_git_clean_ignores_branch_ahead(monkeypatch: pytest.MonkeyPatch):
    _mock_git_status(monkeypatch, "## main...origin/main [ahead 2]\n")

    assert release._git_clean() is True  # noqa: SLF001
    assert release_tasks._is_clean_repository() is True  # noqa: SLF001


def test_git_clean_detects_working_tree_changes(monkeypatch: pytest.MonkeyPatch):
    _mock_git_status(monkeypatch, " M apps/release/release.py\n")

    assert release._git_clean() is False  # noqa: SLF001
    assert release_tasks._is_clean_repository() is False  # noqa: SLF001


def test_git_clean_ignores_log_and_lock_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    log_dir = tmp_path / "logs"
    lock_dir = tmp_path / ".locks"
    log_dir.mkdir()
    lock_dir.mkdir()

    porcelain_output = """## main...origin/main [ahead 2]
?? logs/error.log
?? .locks/release_publish_1.json
"""

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARTHEXIS_LOG_DIR", str(log_dir))
    _mock_git_status(monkeypatch, porcelain_output)

    assert release._git_clean() is True  # noqa: SLF001
    assert release_tasks._is_clean_repository(tmp_path) is True  # noqa: SLF001

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


def test_promote_rejects_dirty_repo_without_stash(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(release, "_git_clean", lambda: False)

    with pytest.raises(release.ReleaseError, match="Git repository is not clean"):
        release.promote(version="1.2.3")


def test_promote_stashes_and_restores(monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str] | tuple[str, dict]] = []

    monkeypatch.setattr(release, "_git_clean", lambda: False)
    monkeypatch.setattr(release, "_git_has_staged_changes", lambda: False)

    def fake_run(cmd, check=True, cwd=None):  # noqa: ANN001, D401
        calls.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(release, "_run", fake_run)
    monkeypatch.setattr(
        release,
        "build",
        lambda **kwargs: calls.append(("build", kwargs)),
    )

    release.promote(version="1.2.3", stash=True)

    assert ["git", "stash", "--include-untracked"] in calls
    assert ["git", "stash", "pop"] in calls
    assert ("build", {"package": release.DEFAULT_PACKAGE, "version": "1.2.3", "creds": None, "tests": False, "dist": True, "git": False, "tag": False, "stash": True}) in calls

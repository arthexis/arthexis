from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.release import release
from apps.release.services import builder
from apps.release.domain import release_tasks


def _mock_git_status(monkeypatch: pytest.MonkeyPatch, output: str) -> None:
    def fake_run(cmd, capture_output=False, text=False, cwd=None):  # noqa: ANN001
        return SimpleNamespace(stdout=output, returncode=0)

    monkeypatch.setattr(builder.subprocess, "run", fake_run)
    monkeypatch.setattr(release_tasks.subprocess, "run", fake_run)
    monkeypatch.setattr(builder, "_is_git_repository", lambda base_dir=None: True)

def test_git_clean_ignores_branch_ahead(monkeypatch: pytest.MonkeyPatch):
    _mock_git_status(monkeypatch, "## main...origin/main [ahead 2]\n")

    assert release._git_clean() is True  # noqa: SLF001
    assert release_tasks._is_clean_repository() is True  # noqa: SLF001

def test_git_clean_detects_working_tree_changes(monkeypatch: pytest.MonkeyPatch):
    _mock_git_status(monkeypatch, " M apps/release/services/__init__.py\n")

    assert release._git_clean() is False  # noqa: SLF001
    assert release_tasks._is_clean_repository() is False  # noqa: SLF001

def test_promote_rejects_dirty_repo_without_stash(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(builder, "_git_clean", lambda: False)

    with pytest.raises(release.ReleaseError, match="Git repository is not clean"):
        release.promote(version="1.2.3")

def test_promote_stashes_and_restores(monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str] | tuple[str, dict]] = []

    monkeypatch.setattr(builder, "_git_clean", lambda: False)
    monkeypatch.setattr(builder, "_git_has_staged_changes", lambda: False)

    def fake_run(cmd, check=True, cwd=None):  # noqa: ANN001, D401
        calls.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(builder, "_run", fake_run)
    monkeypatch.setattr(
        builder,
        "build",
        lambda **kwargs: calls.append(("build", kwargs)),
    )

    release.promote(version="1.2.3", stash=True)

    assert ["git", "stash", "--include-untracked"] in calls
    assert ["git", "stash", "pop"] in calls
    assert ("build", {"package": release.DEFAULT_PACKAGE, "version": "1.2.3", "creds": None, "tests": False, "dist": True, "git": False, "tag": False, "stash": True}) in calls


def test_stage_migration_baselines_adds_existing_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    calls: list[list[str]] = []

    for app_label in release_tasks.MIGRATION_BASELINE_APPS[:2]:
        (tmp_path / "apps" / app_label / "migrations").mkdir(parents=True)

    def fake_run(cmd, *, check=True, cwd=None):  # noqa: ANN001
        calls.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(release_tasks, "_run", fake_run)

    release_tasks._stage_migration_baselines(base_dir=tmp_path)  # noqa: SLF001

    assert calls
    assert calls[0][:3] == ["git", "add", "-A"]
    assert all("/migrations" in path for path in calls[0][3:])


def test_stage_migration_baselines_skips_missing_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    called = False

    def fake_run(cmd, *, check=True, cwd=None):  # noqa: ANN001
        nonlocal called
        called = True
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(release_tasks, "_run", fake_run)

    release_tasks._stage_migration_baselines(base_dir=tmp_path)  # noqa: SLF001

    assert called is False

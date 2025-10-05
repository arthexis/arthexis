import subprocess
from pathlib import Path

import pytest

from core import release


@pytest.fixture
def release_sandbox(tmp_path, monkeypatch):
    """Create a temporary working tree with required files."""

    (tmp_path / "requirements.txt").write_text("example==1.0\n", encoding="utf-8")
    (tmp_path / "VERSION").write_text("0.0.1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_build_requires_clean_repo_without_stash(monkeypatch, release_sandbox):
    monkeypatch.setattr(release, "_git_clean", lambda: False)

    with pytest.raises(release.ReleaseError):
        release.build(version="1.2.3", stash=False)


@pytest.mark.parametrize(
    "twine, expected_message",
    [
        (False, "Release v1.2.3"),
        (True, "PyPI Release v1.2.3"),
    ],
)
def test_build_git_commit_messages(monkeypatch, release_sandbox, twine, expected_message):
    monkeypatch.setattr(release, "_git_clean", lambda: True)
    monkeypatch.setattr(release, "_git_has_staged_changes", lambda: True)

    commands: list[list[str]] = []

    def fake_run(cmd, check=True):
        commands.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(release, "_run", fake_run)

    release.build(version="1.2.3", git=True, twine=twine)

    assert commands == [
        ["git", "add", "VERSION", "pyproject.toml"],
        ["git", "commit", "-m", expected_message],
        ["git", "push"],
    ]


def test_build_creates_and_pushes_tag(monkeypatch, release_sandbox):
    monkeypatch.setattr(release, "_git_clean", lambda: True)

    commands: list[list[str]] = []

    def fake_run(cmd, check=True):
        commands.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(release, "_run", fake_run)

    release.build(version="1.2.3", git=False, tag=True)

    assert commands == [
        ["git", "tag", "v1.2.3"],
        ["git", "push", "origin", "v1.2.3"],
    ]


def test_build_stashes_and_restores_when_requested(monkeypatch, release_sandbox):
    monkeypatch.setattr(release, "_git_clean", lambda: False)

    calls: list[list[str]] = []

    def fake_run(cmd, check=True):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(release, "_run", fake_run)
    monkeypatch.setattr(release, "_write_pyproject", lambda *a, **k: None)

    release.build(version="1.2.3", stash=True)

    assert calls[0] == ["git", "stash", "--include-untracked"]
    assert calls[-1] == ["git", "stash", "pop"]
    assert calls == [
        ["git", "stash", "--include-untracked"],
        ["git", "stash", "pop"],
    ]


def test_build_raises_when_tests_fail(monkeypatch, release_sandbox):
    monkeypatch.setattr(release, "_git_clean", lambda: True)

    class FakeProc:
        def __init__(self):
            self.returncode = 1
            self.stdout = "tests stdout\n"
            self.stderr = "tests stderr\n"

    def fake_run_tests(*, log_path: Path, command=None):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("log", encoding="utf-8")
        return FakeProc()

    monkeypatch.setattr(release, "run_tests", fake_run_tests)

    with pytest.raises(release.TestsFailed) as excinfo:
        release.build(version="1.2.3", tests=True)

    assert excinfo.value.output == "tests stdout\ntests stderr\n"
    assert excinfo.value.log_path == Path("logs/test.log")


def test_promote_commits_only_with_staged_changes(monkeypatch, release_sandbox):
    monkeypatch.setattr(release, "_git_clean", lambda: True)

    build_calls: list[dict[str, object]] = []

    def fake_build(**kwargs):
        build_calls.append(kwargs)

    monkeypatch.setattr(release, "build", fake_build)

    def run_promote(has_staged: bool) -> list[list[str]]:
        calls: list[list[str]] = []

        monkeypatch.setattr(release, "_git_has_staged_changes", lambda: has_staged)

        def fake_run(cmd, check=True):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(release, "_run", fake_run)
        release.promote(version="1.2.3")
        return calls

    calls_with_commit = run_promote(has_staged=True)
    calls_without_commit = run_promote(has_staged=False)

    assert calls_with_commit == [
        ["git", "add", "."],
        ["git", "commit", "-m", "Release v1.2.3"],
    ]
    assert calls_without_commit == [["git", "add", "."]]

    for kwargs in build_calls:
        assert kwargs["dist"] is True
        assert kwargs["git"] is False
        assert kwargs["tag"] is False
        assert kwargs["stash"] is False


def test_promote_requires_clean_repo(monkeypatch, release_sandbox):
    monkeypatch.setattr(release, "_git_clean", lambda: False)

    with pytest.raises(release.ReleaseError):
        release.promote(version="1.2.3")

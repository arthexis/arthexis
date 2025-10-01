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

    def fake_run_tests(*, log_path: Path):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("log", encoding="utf-8")
        return FakeProc()

    monkeypatch.setattr(release, "run_tests", fake_run_tests)

    with pytest.raises(release.TestsFailed) as excinfo:
        release.build(version="1.2.3", tests=True)

    assert excinfo.value.output == "tests stdout\ntests stderr\n"
    assert excinfo.value.log_path == Path("logs/test.log")

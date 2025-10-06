import subprocess
import sys
import types
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


def test_build_removes_shadow_build_package(monkeypatch, release_sandbox):
    monkeypatch.setattr(release, "_git_clean", lambda: True)
    monkeypatch.setattr(release, "_write_pyproject", lambda *a, **k: None)

    build_pkg = release_sandbox / "build"
    build_pkg.mkdir()
    (build_pkg / "__init__.py").write_text("# shadow module", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(cmd, check=True):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(release, "_run", fake_run)

    release.build(version="1.2.3", dist=True)

    assert calls == [[sys.executable, "-m", "build"]]
    assert not build_pkg.exists()


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


class _FakeResponse:
    def __init__(self, version: str):
        self.ok = True
        self._version = version

    def json(self) -> dict[str, dict[str, list[object]]]:
        return {"releases": {self._version: [{}]}}


@pytest.fixture
def _dist_artifacts(monkeypatch):
    fake_files = [
        Path("/tmp/dist/arthexis-1.2.3-py3-none-any.whl"),
        Path("/tmp/dist/arthexis-1.2.3.tar.gz"),
    ]
    original_glob = Path.glob

    def fake_glob(self, pattern):
        if str(self) == "dist":
            return iter(fake_files)
        return original_glob(self, pattern)

    monkeypatch.setattr(Path, "glob", fake_glob)
    return fake_files


def _prepare_release_environment(monkeypatch, *, version: str) -> list[list[str]]:
    monkeypatch.setattr(release, "_git_clean", lambda: True)
    monkeypatch.setattr(release, "_write_pyproject", lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "build", types.ModuleType("build"))

    calls: list[list[str]] = []

    def fake_run(cmd, check=True):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(release, "_run", fake_run)

    def fake_subprocess_run(cmd, capture_output=False, text=False, check=True):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(release.subprocess, "run", fake_subprocess_run)

    fake_requests = types.ModuleType("requests")

    def fake_get(url):
        return _FakeResponse(version)

    fake_requests.get = fake_get  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    return calls


def test_build_twine_checks_existing_versions(monkeypatch, release_sandbox, _dist_artifacts):
    calls = _prepare_release_environment(monkeypatch, version="1.2.3")

    with pytest.raises(release.ReleaseError) as excinfo:
        release.build(version="1.2.3", dist=True, twine=True)

    assert "Version 1.2.3 already on PyPI" in str(excinfo.value)
    assert calls == [[sys.executable, "-m", "build"]]


def test_build_twine_allows_force_upload(monkeypatch, release_sandbox, _dist_artifacts):
    calls = _prepare_release_environment(monkeypatch, version="1.2.3")

    release.build(
        version="1.2.3",
        dist=True,
        twine=True,
        force=True,
        creds=release.Credentials(token="fake-token"),
    )

    assert calls[0] == [sys.executable, "-m", "build"]
    upload_cmd = calls[1]
    assert upload_cmd[:4] == [sys.executable, "-m", "twine", "upload"]
    assert "/tmp/dist/arthexis-1.2.3-py3-none-any.whl" in upload_cmd
    assert "/tmp/dist/arthexis-1.2.3.tar.gz" in upload_cmd
    assert upload_cmd[-4:] == ["--username", "__token__", "--password", "fake-token"]


def test_build_twine_retries_connection_errors(monkeypatch, release_sandbox, _dist_artifacts):
    monkeypatch.setattr(release, "_git_clean", lambda: True)
    monkeypatch.setattr(release, "_write_pyproject", lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "build", types.ModuleType("build"))

    calls: list[list[str]] = []

    def fake_run(cmd, check=True):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(release, "_run", fake_run)

    twine_attempts: list[list[str]] = []

    def fake_subprocess_run(cmd, capture_output=False, text=False, check=True):
        calls.append(list(cmd))
        if "twine" in cmd:
            twine_attempts.append(list(cmd))
            if len(twine_attempts) < 3:
                return subprocess.CompletedProcess(
                    cmd,
                    1,
                    stdout="",
                    stderr="ConnectionResetError: network interruption",
                )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(release.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(release.time, "sleep", lambda *args, **kwargs: None)

    release.build(
        version="1.2.3",
        dist=True,
        twine=True,
        force=True,
        creds=release.Credentials(token="fake-token"),
    )

    assert len(twine_attempts) == 3
    assert calls[0] == [sys.executable, "-m", "build"]


def test_build_twine_retries_and_guides_user(monkeypatch, release_sandbox, _dist_artifacts):
    monkeypatch.setattr(release, "_git_clean", lambda: True)
    monkeypatch.setattr(release, "_write_pyproject", lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "build", types.ModuleType("build"))

    def fake_run(cmd, check=True):
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(release, "_run", fake_run)

    def fake_subprocess_run(cmd, capture_output=False, text=False, check=True):
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="",
            stderr="urllib3.exceptions.ProtocolError: Connection aborted.",
        )

    monkeypatch.setattr(release.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(release.time, "sleep", lambda *args, **kwargs: None)

    with pytest.raises(release.ReleaseError) as excinfo:
        release.build(
            version="1.2.3",
            dist=True,
            twine=True,
            force=True,
            creds=release.Credentials(token="fake-token"),
        )

    message = str(excinfo.value)
    assert "failed after 3 attempts" in message
    assert "Check your internet connection" in message

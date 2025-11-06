import subprocess
import sys
import types
import uuid
from pathlib import Path

import pytest

from django.contrib.auth import get_user_model

from core import release
from core.models import Package, PackageRelease, ReleaseManager


@pytest.fixture
def release_sandbox(tmp_path, monkeypatch):
    """Create a temporary working tree with required files."""

    (tmp_path / "requirements.txt").write_text("example==1.0\n", encoding="utf-8")
    (tmp_path / "VERSION").write_text("0.0.1\n", encoding="utf-8")
    subprocess.run(
        ["git", "init"],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _without_pip_installs(commands: list[list[str]]) -> list[list[str]]:
    return [
        cmd
        for cmd in commands
        if not (len(cmd) >= 3 and cmd[:3] == [sys.executable, "-m", "pip"])
    ]


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

    def fake_run(cmd, check=True, **kwargs):
        calls.append(list(cmd))
        cwd = Path(kwargs.get("cwd", Path.cwd()))
        if cmd[:3] == [sys.executable, "-m", "build"]:
            dist_dir = cwd / "dist"
            dist_dir.mkdir(parents=True, exist_ok=True)
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

    fake_build = types.ModuleType("build")
    fake_build.__file__ = "/usr/lib/python3.12/site-packages/build/__init__.py"
    monkeypatch.setitem(sys.modules, "build", fake_build)

    build_pkg = release_sandbox / "build"
    build_pkg.mkdir()
    (build_pkg / "__init__.py").write_text("# shadow module", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(cmd, check=True, **kwargs):
        calls.append(list(cmd))
        cwd = Path(kwargs.get("cwd", Path.cwd()))
        if cmd[:3] == [sys.executable, "-m", "build"]:
            dist_dir = cwd / "dist"
            dist_dir.mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(release, "_run", fake_run)

    release.build(version="1.2.3", dist=True)

    assert _without_pip_installs(calls) == [[sys.executable, "-m", "build"]]
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


@pytest.mark.django_db
def test_build_publish_targets_ignore_manager_profile_url(monkeypatch):
    for key in ("PYPI_REPOSITORY_URL", "PYPI_API_TOKEN", "PYPI_USERNAME", "PYPI_PASSWORD"):
        monkeypatch.delenv(key, raising=False)

    User = get_user_model()
    user = User.objects.create_user(username="relmgr", password="pwd", email="relmgr@example.com")
    manager = ReleaseManager.objects.create(
        user=user,
        pypi_url="https://pypi.org/user/example/",
    )
    package = Package.objects.create(
        name=f"pkg-{uuid.uuid4().hex}",
        release_manager=manager,
    )
    release_obj = PackageRelease.objects.create(
        package=package,
        release_manager=manager,
        version="1.2.3",
    )

    targets = release_obj.build_publish_targets()

    assert targets[0].repository_url is None


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
    fake_build = types.ModuleType("build")
    fake_build.__file__ = "/usr/lib/python3.12/site-packages/build/__init__.py"
    monkeypatch.setitem(sys.modules, "build", fake_build)

    calls: list[list[str]] = []

    def fake_run(cmd, check=True, **kwargs):
        calls.append(list(cmd))
        cwd = Path(kwargs.get("cwd", Path.cwd()))
        if cmd[:3] == [sys.executable, "-m", "build"]:
            dist_dir = cwd / "dist"
            dist_dir.mkdir(parents=True, exist_ok=True)
            (dist_dir / "artifact.whl").write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(release, "_run", fake_run)

    def fake_subprocess_run(cmd, capture_output=False, text=False, check=True, **kwargs):
        empty = "" if text else b""
        if cmd[:2] == ["git", "ls-files"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=empty, stderr=empty)
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout=empty, stderr=empty)

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
    assert _without_pip_installs(calls) == [[sys.executable, "-m", "build"]]


def test_build_twine_allows_force_upload(monkeypatch, release_sandbox, _dist_artifacts):
    calls = _prepare_release_environment(monkeypatch, version="1.2.3")

    release.build(
        version="1.2.3",
        dist=True,
        twine=True,
        force=True,
        creds=release.Credentials(token="fake-token"),
    )

    filtered_calls = _without_pip_installs(calls)
    assert filtered_calls[0] == [sys.executable, "-m", "build"]
    upload_cmd = filtered_calls[1]
    assert upload_cmd[:4] == [sys.executable, "-m", "twine", "upload"]
    assert "/tmp/dist/arthexis-1.2.3-py3-none-any.whl" in upload_cmd
    assert "/tmp/dist/arthexis-1.2.3.tar.gz" in upload_cmd
    assert upload_cmd[-4:] == ["--username", "__token__", "--password", "fake-token"]


def test_build_twine_retries_connection_errors(monkeypatch, release_sandbox, _dist_artifacts):
    monkeypatch.setattr(release, "_git_clean", lambda: True)
    monkeypatch.setattr(release, "_write_pyproject", lambda *a, **k: None)
    fake_build = types.ModuleType("build")
    fake_build.__file__ = "/usr/lib/python3.12/site-packages/build/__init__.py"
    monkeypatch.setitem(sys.modules, "build", fake_build)

    calls: list[list[str]] = []

    def fake_run(cmd, check=True, **kwargs):
        calls.append(list(cmd))
        cwd = Path(kwargs.get("cwd", Path.cwd()))
        if cmd[:3] == [sys.executable, "-m", "build"]:
            dist_dir = cwd / "dist"
            dist_dir.mkdir(parents=True, exist_ok=True)
            (dist_dir / "artifact.whl").write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(release, "_run", fake_run)

    twine_attempts: list[list[str]] = []

    def fake_subprocess_run(cmd, capture_output=False, text=False, check=True, **kwargs):
        empty = "" if text else b""
        error_output = (
            "ConnectionResetError: network interruption"
            if text
            else b"ConnectionResetError: network interruption"
        )
        if cmd[:2] == ["git", "ls-files"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=empty, stderr=empty)
        calls.append(list(cmd))
        if "twine" in cmd:
            twine_attempts.append(list(cmd))
            if len(twine_attempts) < 3:
                return subprocess.CompletedProcess(cmd, 1, stdout=empty, stderr=error_output)
        return subprocess.CompletedProcess(cmd, 0, stdout=empty, stderr=empty)

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
    assert _without_pip_installs(calls)[0] == [sys.executable, "-m", "build"]


def test_build_twine_retries_and_guides_user(monkeypatch, release_sandbox, _dist_artifacts):
    monkeypatch.setattr(release, "_git_clean", lambda: True)
    monkeypatch.setattr(release, "_write_pyproject", lambda *a, **k: None)
    fake_build = types.ModuleType("build")
    fake_build.__file__ = "/usr/lib/python3.12/site-packages/build/__init__.py"
    monkeypatch.setitem(sys.modules, "build", fake_build)

    def fake_run(cmd, check=True, **kwargs):
        cwd = Path(kwargs.get("cwd", Path.cwd()))
        if cmd[:3] == [sys.executable, "-m", "build"]:
            dist_dir = cwd / "dist"
            dist_dir.mkdir(parents=True, exist_ok=True)
            (dist_dir / "artifact.whl").write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(release, "_run", fake_run)

    def fake_subprocess_run(cmd, capture_output=False, text=False, check=True, **kwargs):
        empty = "" if text else b""
        error_output = (
            "urllib3.exceptions.ProtocolError: Connection aborted."
            if text
            else b"urllib3.exceptions.ProtocolError: Connection aborted."
        )
        return subprocess.CompletedProcess(cmd, 1, stdout=empty, stderr=error_output)

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

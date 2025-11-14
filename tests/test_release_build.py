from __future__ import annotations

import shutil
import stat
import sys
import uuid
from pathlib import Path
from unittest import mock

import pytest

from core import release as release_module


def make_proc(returncode: int, stdout: str = "", stderr: str = ""):
    proc = mock.Mock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def _patch_sleep(monkeypatch):
    sleep = mock.Mock()
    monkeypatch.setattr(release_module.time, "sleep", sleep)
    return sleep


@pytest.mark.django_db
def test_build_sanitizes_runtime_directories(monkeypatch):
    base_dir = Path(__file__).resolve().parents[1]
    locked_dir = base_dir / "run-permission-check"
    locked_dir.mkdir(exist_ok=True)
    locked_dir.chmod(0)
    dist_dir = base_dir / "dist"
    dist_backup = None
    if dist_dir.exists():
        dist_backup = dist_dir.parent / f"dist.backup.{uuid.uuid4().hex}"
        dist_dir.rename(dist_backup)

    run_calls: list[tuple[list[str], Path | None]] = []

    def fake_run(cmd, check=True, cwd=None):
        run_calls.append((cmd, Path(cwd) if cwd else None))
        if cmd[:3] == [sys.executable, "-m", "build"]:
            staging = Path(cwd)
            assert not (staging / locked_dir.name).exists()
            dist_dir = staging / "dist"
            dist_dir.mkdir(parents=True, exist_ok=True)
            (dist_dir / "artifact.whl").write_text("wheel", encoding="utf-8")
        return mock.Mock(returncode=0)

    monkeypatch.setenv("ARTHEXIS_LOG_DIR", str(locked_dir))

    try:
        with mock.patch("core.release._run", side_effect=fake_run):
            release_module._build_in_sanitized_tree(base_dir)

        build_invocations = [
            call for call in run_calls if call[0][:3] == [sys.executable, "-m", "build"]
        ]
        assert build_invocations, "Expected python -m build to run"
        build_cwd = build_invocations[0][1]
        assert build_cwd is not None and build_cwd != base_dir
        assert (base_dir / "dist" / "artifact.whl").exists()
    finally:
        try:
            locked_dir.chmod(stat.S_IRWXU)
        except PermissionError:
            pass
        shutil.rmtree(locked_dir, ignore_errors=True)
        if dist_dir.exists():
            shutil.rmtree(dist_dir)
        if dist_backup is not None:
            if dist_dir.exists():
                shutil.rmtree(dist_dir)
            dist_backup.rename(dist_dir)


def test_upload_with_retries_eventual_success(monkeypatch):
    sleep = _patch_sleep(monkeypatch)
    attempts = [
        make_proc(1, stderr="ConnectionResetError: connection aborted"),
        make_proc(1, stderr="ProtocolError: remote host closed the connection"),
        make_proc(0, stdout="Uploaded"),
    ]

    monkeypatch.setattr(
        release_module.subprocess,
        "run",
        mock.Mock(side_effect=attempts),
    )

    release_module._upload_with_retries(["twine", "upload"], repository="PyPI", retries=3)

    run_mock = release_module.subprocess.run
    assert run_mock.call_count == 3
    assert sleep.call_count == 2


def test_upload_with_retries_exhausts_retryable_errors(monkeypatch):
    sleep = _patch_sleep(monkeypatch)
    errors = [
        make_proc(1, stderr="ConnectionResetError: remote host closed the connection"),
        make_proc(1, stderr="ConnectionResetError: remote host closed the connection"),
        make_proc(1, stderr="ConnectionResetError: remote host closed the connection"),
    ]

    run_mock = mock.Mock(side_effect=errors)
    monkeypatch.setattr(release_module.subprocess, "run", run_mock)

    with pytest.raises(release_module.ReleaseError) as excinfo:
        release_module._upload_with_retries(["twine", "upload"], repository="PyPI", retries=3)

    message = str(excinfo.value)
    assert "failed after 3 attempts" in message
    assert "remote host closed the connection" in message
    assert run_mock.call_count == 3
    assert sleep.call_count == 2


def test_upload_with_retries_non_retryable_failure(monkeypatch):
    sleep = _patch_sleep(monkeypatch)
    run_mock = mock.Mock(return_value=make_proc(1, stderr="HTTP 403 forbidden"))
    monkeypatch.setattr(release_module.subprocess, "run", run_mock)

    with pytest.raises(release_module.ReleaseError) as excinfo:
        release_module._upload_with_retries(["twine", "upload"], repository="PyPI", retries=3)

    assert str(excinfo.value) == "HTTP 403 forbidden"
    run_mock.assert_called_once()
    sleep.assert_not_called()

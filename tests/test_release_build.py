from __future__ import annotations

import shutil
import stat
import sys
import uuid
from pathlib import Path
from unittest import mock

import pytest

from core import release as release_module


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

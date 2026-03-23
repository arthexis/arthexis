"""Tests for validated project Python resolution helpers."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

from utils import python_env


def test_project_python_candidates_include_posix_and_windows_paths(tmp_path: Path) -> None:
    """Candidate resolution should consider both POSIX and Windows virtualenv layouts."""

    assert python_env.project_python_candidates(tmp_path) == (
        tmp_path / ".venv" / "bin" / "python",
        tmp_path / ".venv" / "Scripts" / "python.exe",
    )


def test_resolve_project_python_prefers_posix_venv_binary(tmp_path: Path) -> None:
    """POSIX virtualenv interpreters should win when executable."""

    python_bin = tmp_path / ".venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("#!/bin/sh\n")
    python_bin.chmod(0o755)

    assert python_env.resolve_project_python(tmp_path) == str(python_bin)


def test_resolve_project_python_falls_back_to_current_interpreter(tmp_path: Path) -> None:
    """Current interpreter should be used when no repo virtualenv exists."""

    assert python_env.resolve_project_python(tmp_path) == sys.executable


def test_resolve_project_python_falls_back_when_venv_binary_cannot_start(
    monkeypatch, tmp_path: Path
) -> None:
    """Broken virtualenv entrypoints should fall back to the current interpreter."""

    python_bin = tmp_path / ".venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("#!/missing/python\n")
    python_bin.chmod(0o755)

    def fake_run(cmd, **kwargs):
        if cmd[0] == str(python_bin):
            raise FileNotFoundError("stale shebang")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(python_env.subprocess, "run", fake_run)

    assert python_env.resolve_project_python(tmp_path) == sys.executable


def test_resolve_project_python_accepts_runnable_binary_without_execute_bits(
    monkeypatch, tmp_path: Path
) -> None:
    """Runnable WSL-mounted interpreters should be accepted without POSIX X bits."""

    python_bin = tmp_path / ".venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("#!/bin/sh\n")
    python_bin.chmod(stat.S_IRUSR | stat.S_IWUSR)

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        if cmd[0] == str(python_bin):
            return Result()
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(python_env.subprocess, "run", fake_run)

    assert python_env.resolve_project_python(tmp_path) == str(python_bin)

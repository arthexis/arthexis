"""Tests for validated project Python resolution helpers."""

from __future__ import annotations

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

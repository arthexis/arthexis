"""Helpers for resolving the validated project Python interpreter."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def project_python_candidates(base_dir: Path) -> tuple[Path, ...]:
    """Return candidate interpreter paths for the repository virtual environment.

    Args:
        base_dir: Repository root that may contain the project virtual environment.

    Returns:
        Candidate interpreter paths ordered by platform preference.
    """

    return (
        base_dir / ".venv" / "bin" / "python",
        base_dir / ".venv" / "Scripts" / "python.exe",
    )


def _is_runnable_project_python(candidate: Path) -> bool:
    """Return whether ``candidate`` can start a Python process successfully.

    Args:
        candidate: Candidate interpreter path inside the repository virtualenv.

    Returns:
        ``True`` when the candidate exists and can launch a trivial Python command;
        otherwise ``False``.

    Raises:
        No exceptions are raised. Launch failures are treated as non-runnable
        candidates so the caller can fall back to another interpreter.
    """

    if not candidate.is_file():
        return False

    try:
        result = subprocess.run(
            [str(candidate), "-c", "raise SystemExit(0)"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False

    return result.returncode == 0


def resolve_project_python(base_dir: Path) -> str:
    """Return the preferred interpreter for repository-managed commands.

    Args:
        base_dir: Repository root that may contain the project virtual environment.

    Returns:
        The project virtual environment interpreter when present and runnable;
        otherwise the currently running Python interpreter.
    """

    for candidate in project_python_candidates(base_dir):
        if _is_runnable_project_python(candidate):
            return str(candidate)
    return sys.executable

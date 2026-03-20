"""Helpers for resolving the validated project Python interpreter."""

from __future__ import annotations

import os
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


def resolve_project_python(base_dir: Path) -> str:
    """Return the preferred interpreter for repository-managed commands.

    Args:
        base_dir: Repository root that may contain the project virtual environment.

    Returns:
        The project virtual environment interpreter when present and executable;
        otherwise the currently running Python interpreter.
    """

    for candidate in project_python_candidates(base_dir):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return sys.executable

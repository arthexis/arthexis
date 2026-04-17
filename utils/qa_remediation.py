"""Deterministic remediation hints for local QA/test command wrappers."""

from __future__ import annotations

import json
from pathlib import Path
import sys


def expected_venv_python(base_dir: Path) -> Path:
    """Return the expected repository virtualenv interpreter path."""

    suffix = (
        Path("Scripts/python.exe") if sys.platform == "win32" else Path("bin/python")
    )
    return base_dir / ".venv" / suffix


def find_repo_root(start: Path) -> Path:
    """Return the repository root from ``start`` by searching parent directories."""

    path = start
    while path != path.parent:
        if (path / "manage.py").is_file() or (path / "pyproject.toml").is_file():
            return path
        path = path.parent
    raise FileNotFoundError("Repository root not found from command module path.")


def emit_remediation(
    *,
    code: str,
    command: str,
    retry: str,
) -> str:
    """Return a single-line machine-parseable remediation message."""

    return json.dumps(
        {
            "event": "arthexis.qa.remediation",
            "code": code,
            "command": command,
            "retry": retry,
        },
        sort_keys=True,
    )

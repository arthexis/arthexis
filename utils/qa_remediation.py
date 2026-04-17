"""Deterministic remediation hints for local QA/test command wrappers."""

from __future__ import annotations

import json
from pathlib import Path


def expected_venv_python(base_dir: Path) -> Path:
    """Return the expected repository virtualenv interpreter path."""

    return base_dir / ".venv" / "bin" / "python"


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

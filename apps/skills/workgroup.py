from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings

WORKGROUP_FILENAME = "workgroup.md"
DEFAULT_WORKGROUP_TEMPLATE = """# Workgroup

Global coordination file for named commander-equivalent coordinators and commander-created sub-agents.

## Commander Overview

- Last updated:
- Active commander-equivalent coordinators:
- Active combat:
- Active role locks:
- Active PR or issue ownership:
- Coordination notes:

## Commander-Equivalent Entries

## Agent Entries
"""


def default_codex_home() -> Path:
    configured_home = getattr(settings, "CODEX_HOME", "") or os.environ.get("CODEX_HOME", "")
    if configured_home:
        return Path(configured_home).expanduser()
    return Path.home() / ".codex"


def workgroup_path(*, codex_home: Path | str | None = None) -> Path:
    root = Path(codex_home).expanduser() if codex_home is not None else default_codex_home()
    return root / WORKGROUP_FILENAME


def ensure_workgroup_file(*, codex_home: Path | str | None = None) -> Path:
    path = workgroup_path(codex_home=codex_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(DEFAULT_WORKGROUP_TEMPLATE, encoding="utf-8")
    return path


def read_workgroup_text(*, codex_home: Path | str | None = None) -> str:
    path = ensure_workgroup_file(codex_home=codex_home)
    return path.read_text(encoding="utf-8")

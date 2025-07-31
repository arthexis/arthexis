"""Utilities for the todos app."""

from __future__ import annotations

import re
from pathlib import Path

from .models import Todo

TODO_PATTERN = re.compile(r"#\s*TODO\s*:?(.*)")


def create_todos_from_comments(base_path: str | Path | None = None) -> None:
    """Create :class:`Todo` objects from ``# TODO`` comments in the codebase.

    Parameters
    ----------
    base_path:
        Root directory to scan. Defaults to the project root.
    """

    root = Path(base_path) if base_path else Path(__file__).resolve().parent.parent
    for path in root.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        text = path.read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(text, 1):
            match = TODO_PATTERN.search(line)
            if not match:
                continue
            todo_text = match.group(1).strip()
            Todo.objects.get_or_create(
                text=todo_text,
                file_path=str(path.relative_to(root)),
                line_number=lineno,
            )

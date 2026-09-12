#!/usr/bin/env python3
"""Fail fast on source constructs incompatible with the Python 3.10 floor.

The check intentionally focuses on stdlib/type APIs that compile on newer Python
but fail only when a module is imported under Python 3.10. Syntax compatibility
is covered by parsing every Python file with the interpreter running this script.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "build", "dist"}

# Names added after Python 3.10 that should use compatible alternatives or
# typing_extensions instead.
UNSUPPORTED_FROM_IMPORTS = {
    "datetime": {"UTC"},
    "enum": {"ReprEnum", "StrEnum"},
    "typing": {
        "LiteralString",
        "Never",
        "NotRequired",
        "Required",
        "Self",
        "TypeVarTuple",
        "Unpack",
        "assert_never",
        "assert_type",
        "dataclass_transform",
        "reveal_type",
    },
}

# Existing compatibility bootstrap deliberately patches enum.StrEnum before
# utils.role_app_profiles is imported. Keep this single legacy exception until
# that module is naturally touched; new direct imports remain blocked.
ALLOW_IMPORTS = {
    ("utils/role_app_profiles.py", "enum", "StrEnum"),
}

UNSUPPORTED_ATTRIBUTES = {
    ("datetime", "UTC"),
    ("enum", "ReprEnum"),
    ("enum", "StrEnum"),
    ("asyncio", "TaskGroup"),
    ("asyncio", "timeout"),
}


def iter_python_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        files.append(path)
    return sorted(files)


def dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def check_file(path: Path) -> list[str]:
    rel = path.relative_to(ROOT).as_posix()
    source = path.read_text(encoding="utf-8")
    problems: list[str] = []
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError as exc:
        problems.append(f"{rel}:{exc.lineno or 0}: syntax is not valid on Python {sys.version_info.major}.{sys.version_info.minor}: {exc.msg}")
        return problems

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in UNSUPPORTED_FROM_IMPORTS:
            for alias in node.names:
                name = alias.name
                if name in UNSUPPORTED_FROM_IMPORTS[node.module]:
                    if (rel, node.module, name) in ALLOW_IMPORTS:
                        continue
                    problems.append(
                        f"{rel}:{node.lineno}: from {node.module} import {name} requires Python >3.10"
                    )
        elif isinstance(node, ast.Attribute):
            base = dotted_name(node.value)
            if base and (base, node.attr) in UNSUPPORTED_ATTRIBUTES:
                problems.append(
                    f"{rel}:{node.lineno}: {base}.{node.attr} requires Python >3.10"
                )

    # Direct tomllib imports are safe only when the file also contains an
    # explicit tomli fallback. This simple guard catches accidental new uses
    # without rejecting the compatibility wrappers already present.
    if ("import tomllib" in source or "from tomllib import" in source) and "tomli" not in source:
        problems.append(f"{rel}: direct tomllib use requires a tomli fallback on Python 3.10")

    return problems


def main() -> int:
    problems = [problem for path in iter_python_files() for problem in check_file(path)]
    if problems:
        print("Python 3.10 compatibility violations:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    print("Python 3.10 source compatibility audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

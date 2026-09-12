#!/usr/bin/env python3
"""Fail fast on source constructs incompatible with the Python 3.10 floor.

Every Python file is parsed by the interpreter running this script, so newer
syntax fails immediately. APIs intentionally backfilled by ``utils`` (currently
``datetime.UTC``) are not reported here; smaller compatibility cases stay
source-clean so they do not depend on bootstrap import order.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "build", "dist"}

UNSUPPORTED_FROM_IMPORTS = {
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

# utils.role_app_profiles is the original StrEnum consumer and is deliberately
# covered by the package bootstrap. New direct stdlib StrEnum imports are not.
ALLOW_IMPORTS = {
    ("utils/role_app_profiles.py", "enum", "StrEnum"),
}

UNSUPPORTED_ATTRIBUTES = {
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
        problems.append(
            f"{rel}:{exc.lineno or 0}: syntax is not valid on Python "
            f"{sys.version_info.major}.{sys.version_info.minor}: {exc.msg}"
        )
        return problems

    # The compatibility bootstrap necessarily references the APIs it provides.
    if rel == "utils/__init__.py":
        return problems

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in UNSUPPORTED_FROM_IMPORTS:
            for alias in node.names:
                name = alias.name
                if name in UNSUPPORTED_FROM_IMPORTS[node.module]:
                    if (rel, node.module, name) in ALLOW_IMPORTS:
                        continue
                    problems.append(
                        f"{rel}:{node.lineno}: from {node.module} import {name} "
                        "requires Python >3.10 or a compatibility import"
                    )
        elif isinstance(node, ast.Attribute):
            base = dotted_name(node.value)
            if base and (base, node.attr) in UNSUPPORTED_ATTRIBUTES:
                problems.append(
                    f"{rel}:{node.lineno}: {base}.{node.attr} requires Python >3.10"
                )

    # Raw tomllib remains disallowed: use an explicit tomli fallback so scripts
    # and tests are safe even when imported before the application bootstrap.
    if ("import tomllib" in source or "from tomllib import" in source) and "tomli" not in source:
        problems.append(
            f"{rel}: direct tomllib use requires a tomli fallback on Python 3.10"
        )

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

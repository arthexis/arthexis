#!/usr/bin/env python3
"""Ensure changed pytest files include a PR marker with an ISO date."""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class ValidationError:
    file_path: Path
    message: str


def _is_test_file(path: Path) -> bool:
    name = path.name
    return path.suffix == ".py" and (
        name.startswith("test_") or name.endswith("_test.py") or name.endswith("_tests.py")
    )


def _iter_pr_marker_calls(tree: ast.AST) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "pr":
            continue
        mark_attr = func.value
        if not isinstance(mark_attr, ast.Attribute) or mark_attr.attr != "mark":
            continue
        root = mark_attr.value
        if isinstance(root, ast.Name) and root.id == "pytest":
            calls.append(node)
    return calls


def _is_iso_datetime(value: str) -> bool:
    candidate = value.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(candidate)
    except ValueError:
        return False
    return True


def _normalize_pr_reference(value: str) -> str:
    """Normalize PR references into comparable uppercase values."""

    return value.strip().upper()


def _marker_references(call: ast.Call) -> list[str]:
    """Return normalized string references from marker positional args."""

    references: list[str] = []
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            references.append(_normalize_pr_reference(arg.value))
    return references


def validate_test_file(path: Path, expected_pr: str | None = None) -> list[ValidationError]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [ValidationError(path, f"unable to read file: {exc}")]

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [ValidationError(path, f"unable to parse file: {exc}")]

    calls = _iter_pr_marker_calls(tree)
    if not calls:
        return [
            ValidationError(
                path,
                "missing pytest PR marker; add pytest.mark.pr(<reference>, <iso_datetime>)",
            )
        ]

    has_iso_datetime = False
    has_expected_pr = expected_pr is None
    normalized_expected = _normalize_pr_reference(expected_pr) if expected_pr else None

    for call in calls:
        references = _marker_references(call)
        if normalized_expected and normalized_expected in references:
            has_expected_pr = True

        if len(call.args) < 2:
            continue
        second_arg = call.args[1]
        if isinstance(second_arg, ast.Constant) and isinstance(second_arg.value, str) and _is_iso_datetime(second_arg.value):
            has_iso_datetime = True

    if not has_iso_datetime:
        return [
            ValidationError(
                path,
                "pytest.mark.pr marker must include a second argument with an ISO-8601 datetime string",
            )
        ]

    if not has_expected_pr:
        return [
            ValidationError(
                path,
                f"pytest.mark.pr marker must include reference {expected_pr}",
            )
        ]

    return []


def _default_changed_files() -> list[Path]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=AM"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Optional file paths to validate")
    parser.add_argument(
        "--current-pr",
        default=None,
        help="Expected PR reference that must appear in changed test files.",
    )
    args = parser.parse_args(argv)

    files = [Path(p) for p in args.paths] if args.paths else _default_changed_files()
    target_files = [path for path in files if _is_test_file(path)]

    failures: list[ValidationError] = []
    for path in target_files:
        failures.extend(validate_test_file(path, expected_pr=args.current_pr))

    if not failures:
        return 0

    for failure in failures:
        print(f"{failure.file_path}: {failure.message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

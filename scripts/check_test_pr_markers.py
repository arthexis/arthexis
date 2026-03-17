#!/usr/bin/env python3
"""Ensure staged test additions include a pytest PR origin marker."""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ChangedFile:
    """Represent a staged file path."""

    path: Path


@dataclass
class ValidationError:
    """Represent a validation failure for a file."""

    file_path: Path
    message: str


_TEST_DEF_PATTERN = re.compile(r"^\+\s*(?:async\s+def|def)\s+test_[A-Za-z0-9_]*\s*\(")
_TEST_CLASS_PATTERN = re.compile(r"^\+\s*class\s+Test[A-Za-z0-9_]*\s*(?:\(|:)")


def _is_test_file(path: Path) -> bool:
    """Return whether a path follows the repository's test file naming pattern."""

    name = path.name
    return path.suffix == ".py" and (
        name.startswith("test_") or name.endswith("_test.py") or name.endswith("_tests.py")
    )


def _is_pytest_pr_origin_call(node: ast.AST) -> bool:
    """Return True when ``node`` is ``pytest.mark.pr_origin(...)``."""

    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "pr_origin":
        return False
    mark_attr = func.value
    if not isinstance(mark_attr, ast.Attribute) or mark_attr.attr != "mark":
        return False
    return isinstance(mark_attr.value, ast.Name) and mark_attr.value.id == "pytest"


def _iter_pr_origin_calls(tree: ast.AST) -> list[ast.Call]:
    """Return all ``pytest.mark.pr_origin`` calls in an AST tree."""

    return [node for node in ast.walk(tree) if _is_pytest_pr_origin_call(node)]


def _normalize_pr_reference(value: object) -> str | None:
    """Normalize marker values for case-insensitive comparison."""

    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        cleaned = value.strip().upper()
        return cleaned or None
    return None


def _marker_references(call: ast.Call) -> list[str]:
    """Return normalized PR references from positional marker arguments."""

    references: list[str] = []
    for arg in call.args:
        if isinstance(arg, ast.Constant):
            normalized = _normalize_pr_reference(arg.value)
            if normalized is not None:
                references.append(normalized)
    return references


def validate_test_file(path: Path, expected_pr: str | None = None) -> list[ValidationError]:
    """Validate that a test file includes a usable PR origin marker."""

    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [ValidationError(path, f"unable to read file: {exc}")]

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [ValidationError(path, f"unable to parse file: {exc}")]

    calls = _iter_pr_origin_calls(tree)
    if not calls:
        return [
            ValidationError(
                path,
                "missing pytest PR marker; add pytest.mark.pr_origin(<reference>)",
            )
        ]

    if expected_pr is None:
        return []

    normalized_expected = _normalize_pr_reference(expected_pr)
    if normalized_expected is None:
        return []

    for call in calls:
        if normalized_expected in _marker_references(call):
            return []

    return [
        ValidationError(
            path,
            f"pytest.mark.pr_origin marker must include reference {expected_pr}",
        )
    ]


def _staged_changed_files() -> list[ChangedFile]:
    """Return staged added/modified files for pre-commit checks."""

    result = subprocess.run(
        ["git", "diff", "--cached", "--name-status", "--diff-filter=AM"],
        check=True,
        capture_output=True,
        text=True,
    )

    changed: list[ChangedFile] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        _, raw_path = line.split("\t", 1)
        changed.append(ChangedFile(path=Path(raw_path.strip())))
    return changed


def _file_introduces_new_tests(path: Path) -> bool:
    """Return whether staged changes add test definitions in ``path``."""

    result = subprocess.run(
        ["git", "diff", "--cached", "--unified=0", "--", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )

    for line in result.stdout.splitlines():
        if line.startswith("+++"):
            continue
        if _TEST_DEF_PATTERN.match(line) or _TEST_CLASS_PATTERN.match(line):
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    """Run staged test marker validation for pre-commit usage."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Optional file paths to validate")
    parser.add_argument(
        "--current-pr",
        default=None,
        help="Expected PR reference that must appear in changed test files.",
    )
    args = parser.parse_args(argv)

    if args.paths:
        candidates = [ChangedFile(path=Path(p)) for p in args.paths]
    else:
        candidates = _staged_changed_files()

    target_files = [
        change.path
        for change in candidates
        if _is_test_file(change.path) and _file_introduces_new_tests(change.path)
    ]

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

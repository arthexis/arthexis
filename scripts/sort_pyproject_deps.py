#!/usr/bin/env python3
"""Normalize dependency ordering in ``pyproject.toml``.

This script keeps ``project.dependencies`` and every
``project.optional-dependencies`` array sorted to reduce merge conflicts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import tomllib


class PyprojectNormalizationError(RuntimeError):
    """Raised when dependency blocks cannot be safely located or rewritten."""


PYPROJECT_PATH = Path("pyproject.toml")
PROJECT_DEP_HEADER = "[project]"
OPTIONAL_DEP_HEADER = "[project.optional-dependencies]"
ARRAY_START_TEMPLATE = "{key} = ["


def _format_array(values: list[str]) -> str:
    """Render TOML array values using a stable multiline style."""
    if not values:
        return "[]\n"
    items = "\n".join(f"  {json.dumps(value)}," for value in values)
    return f"[\n{items}\n]\n"


def _replace_array_block(section_text: str, key: str, values: list[str]) -> str:
    """Replace a single ``key = [..]`` array block inside a section body."""
    start_marker = ARRAY_START_TEMPLATE.format(key=key)
    start = section_text.find(start_marker)
    if start == -1:
        raise PyprojectNormalizationError(f"Missing array definition for key: {key}")

    body_start = start + len(start_marker)
    body_end = section_text.find("\n]", body_start)
    if body_end == -1:
        raise PyprojectNormalizationError(f"Unterminated array definition for key: {key}")

    replacement = f"{key} = {_format_array(values)}"
    return f"{section_text[:start]}{replacement}{section_text[body_end + 3 :]}"


def _split_section(text: str, header: str) -> tuple[str, str, str]:
    """Split TOML text into ``before``, ``section``, and ``after`` for ``header``."""
    start = text.find(f"{header}\n")
    if start == -1:
        raise PyprojectNormalizationError(f"Missing section header: {header}")
    next_header = text.find("\n[", start + len(header) + 1)
    if next_header == -1:
        next_header = len(text)
    before = text[:start]
    section = text[start:next_header]
    after = text[next_header:]
    return before, section, after


def normalize_pyproject(content: str) -> str:
    """Return normalized TOML content with sorted dependency arrays."""
    data = tomllib.loads(content)
    project = data.get("project", {})

    deps = sorted(project.get("dependencies", []), key=str.lower)

    optional_groups: dict[str, list[str]] = project.get("optional-dependencies", {})
    sorted_optional_groups = {
        group: sorted(values, key=str.lower) for group, values in optional_groups.items()
    }

    before_project, project_section, after_project = _split_section(content, PROJECT_DEP_HEADER)
    project_section = _replace_array_block(project_section, "dependencies", deps)
    content = f"{before_project}{project_section}{after_project}"

    before_optional, optional_section, after_optional = _split_section(
        content, OPTIONAL_DEP_HEADER
    )
    for group_name, values in sorted_optional_groups.items():
        optional_section = _replace_array_block(optional_section, group_name, values)
    return f"{before_optional}{optional_section}{after_optional}"


def main() -> int:
    """Run normalization in rewrite mode or check-only mode."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate ordering without rewriting the file.",
    )
    args = parser.parse_args()

    original = PYPROJECT_PATH.read_text(encoding="utf-8")
    normalized = normalize_pyproject(original)

    if args.check:
        if original != normalized:
            print("pyproject.toml dependencies are not normalized. Run scripts/sort_pyproject_deps.py")
            return 1
        print("pyproject.toml dependencies are normalized")
        return 0

    if original != normalized:
        PYPROJECT_PATH.write_text(normalized, encoding="utf-8")
        print("Normalized dependency ordering in pyproject.toml")
    else:
        print("No dependency ordering changes required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Validate local skill catalog metadata for the flat SKILLS model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def frontmatter_lines(text: str) -> list[str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return lines[1:index]
    return []


def parse_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    data: dict[str, str] = {}
    for line in frontmatter_lines(text):
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip("'\"")
    return data


def iter_skill_dirs(root: Path, include_system: bool) -> list[Path]:
    return [
        skill_dir
        for skill_dir in sorted(root.iterdir() if root.exists() else [])
        if skill_dir.is_dir() and (include_system or not skill_dir.name.startswith("."))
    ]


def lint_skill_dir(skill_dir: Path, max_chars: int) -> tuple[dict[str, Any] | None, list[dict[str, str]], str]:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return None, [], ""
    frontmatter = parse_frontmatter(skill_md)
    name = frontmatter.get("name", "")
    description = frontmatter.get("description", "")
    total_len = len(name) + 1 + len(description)
    item = {
        "folder": str(skill_dir),
        "name": name,
        "descriptionLength": len(description),
        "nameDescriptionLength": total_len,
        "ok": True,
        "errors": [],
    }
    if not name:
        item["errors"].append("missing name")
    if not description:
        item["errors"].append("missing description")
    if name and skill_dir.name != name:
        item["errors"].append(f"folder/name mismatch: {skill_dir.name} != {name}")
    if total_len > max_chars:
        item["errors"].append(f"name+description length {total_len} exceeds {max_chars}")
    if not item["errors"]:
        return item, [], name
    item["ok"] = False
    errors = [{"skill": name or skill_dir.name, "error": error} for error in item["errors"]]
    return item, errors, name


def duplicate_name_errors(names: dict[str, list[str]]) -> list[dict[str, str]]:
    return [
        {"skill": name, "error": f"duplicate name in {paths}"}
        for name, paths in names.items()
        if name and len(paths) > 1
    ]


def lint(root: Path, max_chars: int, include_system: bool) -> dict[str, Any]:
    items = []
    errors = []
    names: dict[str, list[str]] = {}
    for skill_dir in iter_skill_dirs(root, include_system):
        item, item_errors, name = lint_skill_dir(skill_dir, max_chars)
        if item is None:
            continue
        names.setdefault(name, []).append(str(skill_dir))
        errors.extend(item_errors)
        items.append(item)
    errors.extend(duplicate_name_errors(names))
    return {"root": str(root), "maxNameDescriptionChars": max_chars, "skills": items, "errors": errors, "ok": not errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-root", type=Path, default=Path.home() / ".codex" / "skills")
    parser.add_argument("--max-description-chars", type=int, default=720)
    parser.add_argument("--include-system", action="store_true")
    args = parser.parse_args()
    result = lint(args.skills_root.expanduser(), args.max_description_chars, args.include_system)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

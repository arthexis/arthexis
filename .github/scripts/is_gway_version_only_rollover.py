"""Detect whether one Gway commit only advances project.version by one patch."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


PROJECT_HEADER = "[project]"
VERSION_RE = re.compile(r'^(?P<prefix>\s*version\s*=\s*")(?P<version>[^"]+)(?P<suffix>"\s*)$')


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _project_version(text: str) -> str:
    in_project = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == PROJECT_HEADER:
            in_project = True
            continue
        if in_project and stripped.startswith("["):
            break
        if in_project:
            match = VERSION_RE.match(line)
            if match:
                return match.group("version")
    raise ValueError("project.version not found")


def _normalize_project_version(text: str) -> str:
    lines = text.splitlines(keepends=True)
    in_project = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == PROJECT_HEADER:
            in_project = True
            continue
        if in_project and stripped.startswith("["):
            break
        if in_project:
            body = line.rstrip("\r\n")
            ending = line[len(body):]
            match = VERSION_RE.match(body)
            if match:
                lines[index] = f'{match.group("prefix")}__VERSION__{match.group("suffix")}{ending}'
                return "".join(lines)
    raise ValueError("project.version not found")


def is_patch_rollover(repo: Path, before: str, after: str) -> bool:
    changed = [
        line
        for line in _git(repo, "diff", "--name-only", before, after).splitlines()
        if line
    ]
    if changed != ["pyproject.toml"]:
        return False

    before_text = _git(repo, "show", f"{before}:pyproject.toml")
    after_text = _git(repo, "show", f"{after}:pyproject.toml")
    before_version = _project_version(before_text)
    after_version = _project_version(after_text)

    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", before_version)
    if not match:
        return False
    major, minor, patch = map(int, match.groups())
    if after_version != f"{major}.{minor}.{patch + 1}":
        return False

    return _normalize_project_version(before_text) == _normalize_project_version(after_text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repository", type=Path)
    parser.add_argument("before")
    parser.add_argument("after")
    args = parser.parse_args()
    raise SystemExit(0 if is_patch_rollover(args.repository, args.before, args.after) else 1)


if __name__ == "__main__":
    main()

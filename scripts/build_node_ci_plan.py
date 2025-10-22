#!/usr/bin/env python3
"""Generate the CI matrix for node roles and detect database changes."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Iterable

FIXTURE_ROOT = Path("nodes/fixtures")
FEATURE_FIXTURE_GLOB = "node_features__*.json"
ROLE_FIXTURE_GLOB = "node_roles__*.json"
DEFAULT_ROLES = ["Watchtower", "Control", "Satellite", "Terminal"]

DATABASE_PATTERNS = (
    "*/migrations/*.py",
    "*/models.py",
    "*/models/*.py",
)


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise RuntimeError(f"Invalid JSON in {path}") from exc


def _normalize_roles(value: object) -> list[str]:
    roles: list[str] = []
    if isinstance(value, str):
        roles.append(value)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            if isinstance(item, str):
                roles.append(item)
            elif isinstance(item, (list, tuple)) and item:
                first = item[0]
                if isinstance(first, str):
                    roles.append(first)
    return roles


def load_role_features(fixtures: Path) -> dict[str, set[str]]:
    """Return mapping of node roles to the feature slugs they enable."""

    mapping: dict[str, set[str]] = {}
    if not fixtures.exists():
        return mapping
    for fixture in fixtures.glob(FEATURE_FIXTURE_GLOB):
        data = _load_json(fixture)
        if not isinstance(data, list):
            continue
        for entry in data:
            if not isinstance(entry, dict):
                continue
            fields = entry.get("fields", {})
            if not isinstance(fields, dict):
                continue
            slug = fields.get("slug")
            if not isinstance(slug, str):
                continue
            for role in _normalize_roles(fields.get("roles", [])):
                mapping.setdefault(role, set()).add(slug)
    return mapping


def load_roles(fixtures: Path) -> list[str]:
    """Return sorted list of known node roles."""

    roles: set[str] = set()
    if fixtures.exists():
        for fixture in fixtures.glob(ROLE_FIXTURE_GLOB):
            data = _load_json(fixture)
            if not isinstance(data, list):
                continue
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                fields = entry.get("fields", {})
                if not isinstance(fields, dict):
                    continue
                name = fields.get("name")
                if isinstance(name, str):
                    roles.add(name)
    if not roles:
        roles.update(DEFAULT_ROLES)
    return sorted(roles)


def get_changed_files(base: str) -> list[str]:
    try:
        diff = subprocess.check_output(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:  # pragma: no cover - defensive
        return []
    return [line.strip() for line in diff.splitlines() if line.strip()]


def detect_database_changes(changed: Iterable[str]) -> bool:
    """Return ``True`` when any changed path maps to the database schema."""

    for name in changed:
        path = PurePosixPath(name)
        if any(path.match(pattern) for pattern in DATABASE_PATTERNS):
            return True
    return False


def build_matrix(
    roles: Iterable[str], features: dict[str, set[str]]
) -> list[dict[str, str]]:
    """Return CI matrix entries for each role and its features."""

    matrix: list[dict[str, str]] = []
    for role in roles:
        enabled = ",".join(sorted(features.get(role, set())))
        matrix.append({"role": role, "features": enabled})
    return matrix


def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    changed = get_changed_files(base)
    fixtures = FIXTURE_ROOT
    features = load_role_features(fixtures)
    roles = load_roles(fixtures)
    matrix = build_matrix(roles, features)
    result = {
        "matrix": matrix,
        "database_changed": detect_database_changes(changed),
    }
    json.dump(result, sys.stdout)


if __name__ == "__main__":  # pragma: no cover - script entry
    main()

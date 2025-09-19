#!/usr/bin/env python3
"""Determine which node roles are affected by a diff."""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable

ARCHITECTURE_MANIFEST = Path("ci/architecture_manifest.yml")
FIXTURE_ROOT = Path("nodes/fixtures")
FEATURE_FIXTURE_GLOB = "node_features__*.json"
ROLE_FIXTURE_GLOB = "node_roles__*.json"


@dataclass(slots=True)
class Component:
    """Reusable unit of functionality tied to one or more node roles."""

    name: str
    paths: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Manifest:
    """Structured representation of the architecture manifest."""

    components: dict[str, Component] = field(default_factory=dict)
    shared_globs: list[str] = field(default_factory=list)


def _ensure_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return [str(value)]


def load_manifest(path: Path) -> Manifest:
    """Load component definitions and shared globs from the manifest file."""

    if not path.exists():
        return Manifest()
    text = path.read_text()
    data: dict[str, object]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:  # pragma: no cover - optional dependency
            import yaml  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "Unable to parse architecture manifest without PyYAML"
            ) from exc
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise TypeError("Architecture manifest must be a mapping")
    else:
        if not isinstance(data, dict):
            raise TypeError("Architecture manifest must be a mapping")

    components: dict[str, Component] = {}
    raw_components = data.get("components", {})
    if isinstance(raw_components, dict):
        for name, payload in raw_components.items():
            if not isinstance(payload, dict):
                continue
            components[str(name)] = Component(
                name=str(name),
                paths=_ensure_list(payload.get("paths")),
                features=_ensure_list(payload.get("features")),
                roles=_ensure_list(payload.get("roles")),
            )

    shared_globs = _ensure_list(data.get("shared_globs"))
    return Manifest(components=components, shared_globs=shared_globs)


def _match_any(path: PurePosixPath, patterns: Iterable[str]) -> bool:
    return any(path.match(pattern) for pattern in patterns)


def load_feature_roles(fixtures: Path) -> dict[str, set[str]]:
    """Return a mapping of NodeFeature slugs to the roles they enable."""

    mapping: dict[str, set[str]] = {}
    if not fixtures.exists():
        return mapping
    for fixture in fixtures.glob(FEATURE_FIXTURE_GLOB):
        try:
            data = json.loads(fixture.read_text())
        except json.JSONDecodeError:  # pragma: no cover - defensive
            continue
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
            roles: set[str] = set()
            for role in fields.get("roles", []):
                if isinstance(role, str):
                    roles.add(role)
                elif isinstance(role, (list, tuple)) and role:
                    first = role[0]
                    if isinstance(first, str):
                        roles.add(first)
            if roles:
                mapping[slug] = roles
    return mapping


def load_known_roles(
    fixtures: Path, feature_roles: dict[str, set[str]], manifest: Manifest
) -> set[str]:
    """Collect the canonical list of roles for fallbacks."""

    roles: set[str] = set()
    for component in manifest.components.values():
        roles.update(component.roles)
    for values in feature_roles.values():
        roles.update(values)
    if fixtures.exists():
        for fixture in fixtures.glob(ROLE_FIXTURE_GLOB):
            try:
                data = json.loads(fixture.read_text())
            except json.JSONDecodeError:  # pragma: no cover - defensive
                continue
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
    return roles


def roles_for_components(
    components: Iterable[str],
    manifest: Manifest,
    feature_roles: dict[str, set[str]],
) -> set[str]:
    roles: set[str] = set()
    for name in components:
        component = manifest.components.get(name)
        if not component:
            continue
        roles.update(component.roles)
        for feature in component.features:
            roles.update(feature_roles.get(feature, set()))
    return roles


def impacted_roles(
    changed: Iterable[str],
    manifest: Manifest,
    feature_roles: dict[str, set[str]],
    all_roles: Iterable[str],
) -> list[str]:
    candidate_roles: set[str] = set()
    impacted_components: set[str] = set()
    for name in changed:
        path = PurePosixPath(name)
        if manifest.shared_globs and _match_any(path, manifest.shared_globs):
            return sorted(set(all_roles))
        for component_name, component in manifest.components.items():
            if component.paths and _match_any(path, component.paths):
                impacted_components.add(component_name)
    candidate_roles.update(roles_for_components(impacted_components, manifest, feature_roles))
    if candidate_roles:
        return sorted(candidate_roles)
    return sorted(set(all_roles))


def get_changed_files(base: str) -> list[str]:
    try:
        diff = subprocess.check_output(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return []
    return [line.strip() for line in diff.splitlines() if line.strip()]


def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    changed = get_changed_files(base)
    manifest = load_manifest(ARCHITECTURE_MANIFEST)
    feature_roles = load_feature_roles(FIXTURE_ROOT)
    known_roles = load_known_roles(FIXTURE_ROOT, feature_roles, manifest)
    if not known_roles:
        known_roles = {"Constellation", "Control", "Satellite", "Terminal"}
    if not changed:
        roles = sorted(known_roles)
    else:
        roles = impacted_roles(changed, manifest, feature_roles, known_roles)
    json.dump(roles, sys.stdout)


if __name__ == "__main__":  # pragma: no cover - script entry
    main()

#!/usr/bin/env python3
"""Determine which node roles are affected by a diff."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

MANIFEST = Path("node_roles.yml")


def load_manifest(path: Path) -> dict[str, list[str]]:
    """Load role to path pattern mapping from a YAML file.

    The repository does not rely on PyYAML, so fall back to a minimal parser if
    the module is unavailable.
    """

    try:  # pragma: no cover - optional dependency
        import yaml  # type: ignore

        with path.open() as fh:
            data = yaml.safe_load(fh) or {}
        return {str(k): list(v or []) for k, v in data.items()}
    except Exception:
        # Very small subset of YAML: ``role:\n  - pattern``
        result: dict[str, list[str]] = {}
        current: str | None = None
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.endswith(":"):
                current = line[:-1]
                result[current] = []
            elif line.startswith("-") and current:
                result[current].append(line[1:].strip())
        return result


def match_roles(files: list[str], manifest: dict[str, list[str]]) -> list[str]:
    roles: set[str] = set()
    for name in files:
        p = Path(name)
        for role, patterns in manifest.items():
            for pat in patterns:
                if p.match(pat):
                    roles.add(role)
    return sorted(roles)


def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    try:
        diff = subprocess.check_output(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        changed = [line.strip() for line in diff.splitlines() if line.strip()]
    except subprocess.CalledProcessError:
        changed = []
    manifest = load_manifest(MANIFEST)
    roles = match_roles(changed, manifest)
    if not roles:
        roles = sorted(manifest.keys())
    json.dump(roles, sys.stdout)


if __name__ == "__main__":  # pragma: no cover - script entry
    main()

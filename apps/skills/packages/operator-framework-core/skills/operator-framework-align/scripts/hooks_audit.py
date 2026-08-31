#!/usr/bin/env python3
"""List local and repo hook surfaces for deterministic Codex integration."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def default_repo() -> Path:
    return Path(os.environ.get("ARTHEXIS_REPO", Path.home() / "Repos" / "arthexis")).expanduser()


def list_files(root: Path) -> list[str]:
    if not root.exists():
        return []
    return [
        str(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--repo", type=Path, default=default_repo())
    args = parser.parse_args()

    codex_home = args.codex_home.expanduser()
    repo = args.repo.expanduser()
    surfaces = {
        "codexHooks": list_files(codex_home / "hooks"),
        "codexScriptsHooks": list_files(codex_home / "scripts" / "hooks"),
        "repoCodexHooks": list_files(repo / ".codex" / "hooks"),
        "repoHooks": list_files(repo / "hooks"),
        "repoHookContext": [str(path) for path in [repo / "apps" / "skills" / "hook_context.py"] if path.exists()],
        "repoHookCommands": list_files(repo / "apps" / "skills" / "management" / "commands"),
    }
    print(json.dumps({"codexHome": str(codex_home), "repo": str(repo), "surfaces": surfaces}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

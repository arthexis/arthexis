#!/usr/bin/env python3
"""Report Arthexis checkout bootstrap state."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any


def default_repo() -> Path:
    env = os.environ.get("ARTHEXIS_REPO")
    if env:
        return Path(env).expanduser()
    cwd = Path.cwd()
    for path in [cwd, *cwd.parents]:
        if (path / "manage.py").exists():
            return path
    return Path.home() / "Repos" / "arthexis"


def run_git(repo: Path, args: list[str]) -> str:
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=True)
        return proc.stdout.strip()
    except Exception as exc:
        return f"ERROR: {exc}"


def python_path(repo: Path) -> Path:
    if platform.system() == "Windows":
        return repo / ".venv" / "Scripts" / "python.exe"
    return repo / ".venv" / "bin" / "python"


def inspect(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    py = python_path(repo)
    scripts = {
        "install_bat": repo / "install.bat",
        "install_sh": repo / "install.sh",
        "upgrade_bat": repo / "upgrade.bat",
        "upgrade_sh": repo / "upgrade.sh",
        "env_refresh_bat": repo / "env-refresh.bat",
        "env_refresh_sh": repo / "env-refresh.sh",
        "manage_py": repo / "manage.py",
        "import_resolution": repo / "scripts" / "check_import_resolution.py",
    }
    return {
        "repo": str(repo),
        "platform": platform.system(),
        "exists": repo.exists(),
        "venv_python": str(py),
        "venv_python_exists": py.exists(),
        "scripts": {name: {"path": str(path), "exists": path.exists()} for name, path in scripts.items()},
        "git_branch": run_git(repo, ["branch", "--show-current"]) if repo.exists() else "",
        "git_status_short": run_git(repo, ["status", "--short"]) if repo.exists() else "",
        "head": run_git(repo, ["rev-parse", "--short", "HEAD"]) if repo.exists() else "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=default_repo())
    args = parser.parse_args()
    print(json.dumps(inspect(args.repo), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

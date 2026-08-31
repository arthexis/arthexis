#!/usr/bin/env python3
"""Plan or run Arthexis install, upgrade, health, and validation commands."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

MANAGE_PY = "manage.py"


def default_repo() -> Path:
    env = os.environ.get("ARTHEXIS_REPO")
    if env:
        return Path(env).expanduser()
    cwd = Path.cwd()
    for path in [cwd, *cwd.parents]:
        if (path / "manage.py").exists():
            return path
    return Path.home() / "Repos" / "arthexis"


def is_windows() -> bool:
    return platform.system() == "Windows"


def venv_python(repo: Path) -> Path:
    return repo / ".venv" / ("Scripts/python.exe" if is_windows() else "bin/python")


def command_exists(path: Path) -> bool:
    return path.exists() and path.is_file()


def script_command(repo: Path, base: str, args: list[str]) -> list[str] | None:
    if is_windows() and command_exists(repo / f"{base}.bat"):
        return ["cmd", "/c", f"{base}.bat", *args]
    if command_exists(repo / f"{base}.sh"):
        return ["bash", f"./{base}.sh", *args]
    return None


def python_command(repo: Path, args: list[str]) -> list[str]:
    py = venv_python(repo)
    executable = str(py) if py.exists() else sys.executable
    return [executable, *args]


def build_plan(repo: Path, action: str, latest: bool, role: str | None) -> list[dict[str, Any]]:
    repo = repo.resolve()
    latest_args = ["--latest"] if latest else []
    env = {}
    if role:
        env["NODE_ROLE"] = role

    commands: list[dict[str, Any]] = []
    if action in {"plan", "install", "all"}:
        install = script_command(repo, "install", [])
        if install:
            commands.append({"name": "install", "cmd": install, "env": env})

    if action in {"plan", "upgrade", "all"}:
        upgrade = script_command(repo, "upgrade", latest_args)
        if upgrade:
            commands.append({"name": "upgrade", "cmd": upgrade, "env": env})

    if action in {"plan", "health", "all"}:
        commands.append({"name": "django-check", "cmd": python_command(repo, [MANAGE_PY, "check"]), "env": env})

    if action in {"plan", "validate", "all"}:
        commands.append({"name": "django-check", "cmd": python_command(repo, [MANAGE_PY, "check"]), "env": env})
        commands.append(
            {
                "name": "makemigrations-check",
                "cmd": python_command(repo, [MANAGE_PY, "makemigrations", "--check", "--dry-run"]),
                "env": env,
            }
        )
        import_resolution = repo / "scripts" / "check_import_resolution.py"
        if import_resolution.exists():
            commands.append({"name": "import-resolution", "cmd": python_command(repo, [str(import_resolution)]), "env": env})

    seen: set[tuple[str, tuple[str, ...]]] = set()
    unique: list[dict[str, Any]] = []
    for item in commands:
        key = (item["name"], tuple(item["cmd"]))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def run_plan(repo: Path, commands: list[dict[str, Any]], timeout: int) -> list[dict[str, Any]]:
    results = []
    for item in commands:
        env = os.environ.copy()
        env.update(item.get("env") or {})
        proc = subprocess.run(item["cmd"], cwd=repo, env=env, text=True, capture_output=True, timeout=timeout)
        results.append(
            {
                "name": item["name"],
                "cmd": item["cmd"],
                "returncode": proc.returncode,
                "stdout": proc.stdout[-4000:],
                "stderr": proc.stderr[-4000:],
            }
        )
        if proc.returncode != 0:
            break
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "install", "upgrade", "health", "validate", "all"], nargs="?", default="plan")
    parser.add_argument("--repo", type=Path, default=default_repo())
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--role")
    parser.add_argument("--write", action="store_true", help="Execute the planned commands")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    repo = args.repo.resolve()
    commands = build_plan(repo, args.action, args.latest, args.role)
    output: dict[str, Any] = {"repo": str(repo), "action": args.action, "write": args.write, "commands": commands}
    if args.write:
        output["results"] = run_plan(repo, commands, args.timeout)
    print(json.dumps(output, indent=2))
    if args.write and any(item.get("returncode") for item in output.get("results", [])):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

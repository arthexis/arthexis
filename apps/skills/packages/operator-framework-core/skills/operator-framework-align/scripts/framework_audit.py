#!/usr/bin/env python3
"""Audit local Arthexis SKILLS, AGENTS, and HOOKS framework alignment."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

AGENTS_MD = "AGENTS.md"
RETIRED_LANGUAGE_NEEDLES = ["operator-manual", "workgroup.md", "agentic personal", "agent personality"]


def default_repo() -> Path:
    return Path(os.environ.get("ARTHEXIS_REPO", Path.home() / "Repos" / "arthexis")).expanduser()


def run_py(script: Path, args: list[str]) -> dict[str, Any]:
    proc = subprocess.run([sys.executable, str(script), *args], text=True, capture_output=True)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        data = {"stdout": proc.stdout}
    return {"returncode": proc.returncode, "data": data, "stderr": proc.stderr.strip()}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def local_agents_checks(target: Path) -> dict[str, Any]:
    text = read(target)
    lower_text = text.lower()
    affirmative_text = "\n".join(
        line for line in text.splitlines() if "not as a personality" not in line.lower()
    )
    affirmative_lower = affirmative_text.lower()
    retired_read = (
        "operator-manual\\skill.md" in lower_text
        or "operator-manual/skill.md" in lower_text
        or ("read" in lower_text and "operator-manual" in lower_text)
    )
    workgroup_required = any(
        phrase in lower_text
        for phrase in (
            "before taking ownership",
            "record workgroup",
            "record the workgroup",
            "record in workgroup",
        )
    )
    agent_personality = any(
        phrase in affirmative_lower
        for phrase in (
            "agentic personalit",
            "agent personality",
            "stable pseudonymous",
            "nickname reuse",
            "agent name",
        )
    )
    return {
        "target": str(target),
        "exists": target.exists(),
        "retiredOperatorManualRequired": retired_read,
        "workgroupBookkeepingRequired": workgroup_required,
        "agentPersonalityLanguage": agent_personality,
        "ok": target.exists() and not retired_read and not workgroup_required and not agent_personality,
    }


def iter_text_files(path: Path) -> list[Path]:
    if not path.exists():
        return []
    if path.is_file():
        return [path]
    return [
        item
        for item in path.rglob("*")
        if item.is_file() and "__pycache__" not in item.parts and item.suffix not in {".pyc", ".sqlite3"}
    ]


def matching_needle(file_path: Path) -> str:
    try:
        lower = file_path.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return ""
    return next((needle for needle in RETIRED_LANGUAGE_NEEDLES if needle in lower), "")


def scan_repo_language(repo: Path) -> dict[str, Any]:
    roots = [repo / AGENTS_MD, repo / "docs", repo / "skills", repo / "apps" / "skills"]
    hits = []
    for root in roots:
        for file_path in iter_text_files(root):
            needle = matching_needle(file_path)
            if needle:
                hits.append({"file": str(file_path), "needle": needle})
    return {"hits": hits, "ok": not hits}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--repo", type=Path, default=default_repo())
    parser.add_argument("--write", action="store_true", help="Write aligned local AGENTS.md")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    codex_home = args.codex_home.expanduser()
    repo = args.repo.expanduser()
    result: dict[str, Any] = {
        "codexHome": str(codex_home),
        "repo": str(repo),
        "localAgents": local_agents_checks(Path.home() / AGENTS_MD),
        "skillCatalog": run_py(here / "skill_catalog_lint.py", ["--skills-root", str(codex_home / "skills")]),
        "hooks": run_py(here / "hooks_audit.py", ["--codex-home", str(codex_home), "--repo", str(repo)]),
        "repoLanguage": scan_repo_language(repo),
    }
    if args.write:
        result["localAgentsSync"] = run_py(here / "local_agents_sync.py", ["--write"])
        result["localAgents"] = local_agents_checks(Path.home() / AGENTS_MD)
    result["ok"] = (
        result["localAgents"]["ok"]
        and result["skillCatalog"]["returncode"] == 0
        and result["hooks"]["returncode"] == 0
        and result["repoLanguage"]["ok"]
    )
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

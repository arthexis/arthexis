#!/usr/bin/env python3
"""Audit local Arthexis SKILLS, AGENTS, and HOOKS framework alignment."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

AGENTS_MD = "AGENTS.md"


def default_repo() -> Path:
    return Path(
        os.environ.get("ARTHEXIS_REPO", Path.home() / "Repos" / "arthexis")
    ).expanduser()


def run_py(script: Path, args: list[str]) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(script), *args], text=True, capture_output=True
    )
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        data = {"stdout": proc.stdout}
    return {"returncode": proc.returncode, "data": data, "stderr": proc.stderr.strip()}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def code_reference_line(line: str, normalized: str) -> bool:
    """Return True when a line most likely references code instead of prose guidance."""
    stripped = line.lstrip()
    if stripped.startswith(("-", "*")):
        return False
    if stripped.startswith(("return ", "def ", "class ", "import ", "from ")):
        return True
    if stripped.startswith(("if ", "elif ", "for ", "while ")) and (
        (stripped.rstrip().endswith(":") and (stripped.count('"') >= 2 or stripped.count("'") >= 2))
        or bool(re.search(r"\bin\s+(normalized|lower)(?:\(\))?\b", normalized))
    ):
        return True
    if stripped.startswith(('"', "'")) and stripped.endswith(('"', "'")) and ":" not in stripped:
        return True
    if re.search(r"\.write_text\s*\(\s*['\"].*?['\"]", stripped):
        return True
    return bool(re.search(r"\bin\s+(normalized|lower)(?:\(\))?\b(?=\s*(?:[:),\]]|\bfor\b))", normalized))


def stale_language_needle(line: str) -> str:
    """Classify a single line for retired-language hits, returning a needle label or empty when negated."""
    lower = line.lower()
    normalized = lower.replace("`", "")
    if not normalized.strip():
        return ""
    if code_reference_line(line, normalized):
        return ""
    retired_language = "retired language" in normalized
    operator_manual_negated = (
        any(
            phrase in normalized
            for phrase in (
                "do not require operator-manual",
                "do not require operator manual",
            )
        )
        or retired_language
        or (
            "retired" in normalized
            and any(
                token in normalized for token in ("operator-manual", "operator manual")
            )
        )
    )
    workgroup_negated = (
        "do not require workgroup.md" in normalized
        or "do not require workgroup md" in normalized
        or retired_language
    )
    personality_negated = (
        any(
            phrase in normalized
            for phrase in (
                "not as a personality",
                "not as a coordination role",
            )
        )
        or retired_language
    )

    if "operator-manual" in normalized or "operator manual" in normalized:
        if operator_manual_negated:
            return ""
        return "operator-manual"
    if "workgroup.md" in normalized or "workgroup md" in normalized:
        if workgroup_negated:
            return ""
        return "workgroup.md"
    if any(
        phrase in normalized for phrase in ("agentic personal", "agent personality")
    ):
        return "" if personality_negated else "agent personality"
    if any(
        phrase in normalized
        for phrase in ("stable pseudonymous", "nickname reuse", "agent name")
    ):
        return "" if personality_negated else "agent personality"
    return ""


def local_agents_checks(target: Path) -> dict[str, Any]:
    text = read(target)
    retired_read = False
    workgroup_required = False
    agent_personality = False
    for line in text.splitlines():
        needle = stale_language_needle(line)
        retired_read = retired_read or needle == "operator-manual"
        workgroup_required = (
            workgroup_required
            or needle == "workgroup.md"
            or (
                "before taking ownership" in line.lower()
                or "record workgroup" in line.lower()
                or "record the workgroup" in line.lower()
                or "record in workgroup" in line.lower()
            )
        )
        agent_personality = agent_personality or needle == "agent personality"

    return {
        "target": str(target),
        "exists": target.exists(),
        "retiredOperatorManualRequired": retired_read,
        "workgroupBookkeepingRequired": workgroup_required,
        "agentPersonalityLanguage": agent_personality,
        "ok": target.exists()
        and not retired_read
        and not workgroup_required
        and not agent_personality,
    }


def iter_text_files(path: Path) -> list[Path]:
    if not path.exists():
        return []
    if path.is_file():
        return [path]
    return [
        item
        for item in path.rglob("*")
        if item.is_file()
        and "__pycache__" not in item.parts
        and item.suffix not in {".pyc", ".sqlite3"}
    ]


def matching_needle(file_path: Path) -> dict[str, Any] | None:
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line_number, line in enumerate(text.splitlines(), start=1):
        needle = stale_language_needle(line)
        if needle:
            return {
                "file": str(file_path),
                "line": line_number,
                "needle": needle,
                "text": line.strip(),
            }
    return None


def scan_repo_language(repo: Path) -> dict[str, Any]:
    roots = [repo / AGENTS_MD, repo / "docs", repo / "skills", repo / "apps" / "skills"]
    skipped_files = {
        (
            repo
            / "apps/skills/packages/operator-framework-core/skills/operator-framework-align/scripts/framework_audit.py"
        ).resolve(),
    }
    hits = []
    for root in roots:
        for file_path in iter_text_files(root):
            if file_path.resolve() in skipped_files:
                continue
            hit = matching_needle(file_path)
            if hit:
                hits.append(hit)
    return {"hits": hits, "ok": not hits}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--repo", type=Path, default=default_repo())
    parser.add_argument(
        "--write", action="store_true", help="Write aligned local AGENTS.md"
    )
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    codex_home = args.codex_home.expanduser()
    repo = args.repo.expanduser()
    result: dict[str, Any] = {
        "codexHome": str(codex_home),
        "repo": str(repo),
        "localAgents": local_agents_checks(Path.home() / AGENTS_MD),
        "skillCatalog": run_py(
            here / "skill_catalog_lint.py",
            ["--skills-root", str(codex_home / "skills")],
        ),
        "hooks": run_py(
            here / "hooks_audit.py",
            ["--codex-home", str(codex_home), "--repo", str(repo)],
        ),
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

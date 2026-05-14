#!/usr/bin/env python3
"""Inspect and optionally merge Arthexis pull requests through GitHub CLI."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from typing import Any

PR_FIELDS = ",".join(
    [
        "number",
        "title",
        "author",
        "headRefName",
        "baseRefName",
        "isDraft",
        "mergeStateStatus",
        "reviewDecision",
        "statusCheckRollup",
        "url",
        "updatedAt",
    ]
)

GOOD_CHECKS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
BAD_CHECKS = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}
BAD_MERGE_STATES = {"DIRTY", "BLOCKED", "BEHIND"}


def require_gh() -> None:
    if not shutil.which("gh"):
        raise SystemExit("gh CLI not found on PATH")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def gh_json(args: list[str]) -> Any:
    require_gh()
    proc = run(["gh", *args])
    if not proc.stdout.strip():
        return None
    return json.loads(proc.stdout)


def list_prs(repo: str, limit: int) -> list[dict[str, Any]]:
    data = gh_json(["pr", "list", "--repo", repo, "--state", "open", "--limit", str(limit), "--json", PR_FIELDS])
    return data or []


def view_pr(repo: str, number: int) -> dict[str, Any]:
    data = gh_json(["pr", "view", str(number), "--repo", repo, "--json", PR_FIELDS + ",body,comments,reviews,commits"])
    return data or {}


def author_login(pr: dict[str, Any]) -> str:
    author = pr.get("author") or {}
    if isinstance(author, dict):
        return str(author.get("login") or "")
    return str(author)


def is_dependabot(pr: dict[str, Any]) -> bool:
    login = author_login(pr).lower()
    title = str(pr.get("title") or "").lower()
    return "dependabot" in login or title.startswith("build(deps")


def check_rollup_state(pr: dict[str, Any]) -> tuple[list[str], list[str]]:
    failing: list[str] = []
    pending: list[str] = []
    for check in pr.get("statusCheckRollup") or []:
        name = str(check.get("name") or check.get("workflowName") or check.get("context") or "check")
        conclusion = str(check.get("conclusion") or "").upper()
        status = str(check.get("status") or "").upper()
        if conclusion in BAD_CHECKS:
            failing.append(f"{name}:{conclusion}")
        elif conclusion and conclusion not in GOOD_CHECKS:
            failing.append(f"{name}:{conclusion}")
        elif status and status not in {"COMPLETED", "SUCCESS"}:
            pending.append(f"{name}:{status}")
        elif not conclusion and not status:
            pending.append(f"{name}:UNKNOWN")
    return failing, pending


def readiness(pr: dict[str, Any], require_approval: bool, allow_pending: bool) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    if pr.get("isDraft"):
        blockers.append("draft")

    merge_state = str(pr.get("mergeStateStatus") or "").upper()
    if merge_state in BAD_MERGE_STATES:
        blockers.append(f"merge_state:{merge_state}")
    elif merge_state in {"UNKNOWN", ""}:
        warnings.append(f"merge_state:{merge_state or 'EMPTY'}")

    review = str(pr.get("reviewDecision") or "").upper()
    if review in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
        blockers.append(f"review:{review}")
    elif require_approval and review != "APPROVED":
        blockers.append(f"review:{review or 'MISSING_APPROVAL'}")

    failing, pending = check_rollup_state(pr)
    blockers.extend(f"check:{item}" for item in failing)
    if pending and not allow_pending:
        blockers.extend(f"pending:{item}" for item in pending)
    elif pending:
        warnings.extend(f"pending:{item}" for item in pending)

    return {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "author": author_login(pr),
        "url": pr.get("url"),
        "dependabot": is_dependabot(pr),
        "ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "mergeStateStatus": pr.get("mergeStateStatus"),
        "reviewDecision": pr.get("reviewDecision"),
        "updatedAt": pr.get("updatedAt"),
    }


def print_table(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        state = "READY" if row["ready"] else "BLOCKED"
        blockers = ", ".join(row["blockers"]) if row["blockers"] else "-"
        warnings = ", ".join(row["warnings"]) if row["warnings"] else "-"
        print(f"#{row['number']} {state} {row['title']}")
        print(f"  author={row['author']} dependabot={row['dependabot']} updated={row.get('updatedAt')}")
        print(f"  blockers={blockers}")
        print(f"  warnings={warnings}")
        print(f"  {row.get('url')}")


def merge_pr(repo: str, number: int, method: str, delete_branch: bool) -> dict[str, Any]:
    cmd = ["gh", "pr", "merge", str(number), "--repo", repo, f"--{method}"]
    if delete_branch:
        cmd.append("--delete-branch")
    proc = run(cmd, check=False)
    return {
        "number": number,
        "command": cmd,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def handle_inspect(args: argparse.Namespace) -> int:
    if not args.pr:
        raise SystemExit("--pr is required for inspect")
    data = view_pr(args.repo, args.pr)
    result = {"pullRequest": data, "readiness": readiness(data, args.require_approval, args.allow_pending)}
    print(json.dumps(result, indent=2) if args.json else json.dumps(result["readiness"], indent=2))
    return 0


def handle_list(rows: list[dict[str, Any]], emit_json: bool) -> int:
    if emit_json:
        print(json.dumps(rows, indent=2))
    else:
        print_table(rows)
    return 0


def handle_merge_ready(args: argparse.Namespace, ready: list[dict[str, Any]]) -> int:
    if not args.write:
        if args.json:
            print(json.dumps(ready, indent=2))
        else:
            print_table(ready)
            print(f"ready_count={len(ready)}")
        return 0

    results = []
    for row in ready:
        if not row["dependabot"] and not args.allow_non_dependabot:
            results.append({"number": row["number"], "skipped": "non-dependabot requires --allow-non-dependabot"})
            continue
        results.append(merge_pr(args.repo, int(row["number"]), args.merge_method, args.delete_branch))
    print(json.dumps({"ready": ready, "mergeResults": results}, indent=2))
    return 0 if all(item.get("returncode", 0) == 0 for item in results if "returncode" in item) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["list", "inspect", "merge-ready"])
    parser.add_argument("--repo", default="arthexis/arthexis")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--pr", type=int)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-approval", action="store_true")
    parser.add_argument("--allow-pending", action="store_true")
    parser.add_argument("--write", action="store_true", help="Perform merges for ready PRs")
    parser.add_argument("--allow-non-dependabot", action="store_true")
    parser.add_argument("--merge-method", choices=["squash", "merge", "rebase"], default="squash")
    parser.add_argument("--delete-branch", action="store_true")
    args = parser.parse_args()

    if args.command == "inspect":
        return handle_inspect(args)

    prs = list_prs(args.repo, args.limit)
    rows = [readiness(pr, args.require_approval, args.allow_pending) for pr in prs]

    if args.command == "list":
        return handle_list(rows, args.json)

    ready = [row for row in rows if row["ready"]]
    return handle_merge_ready(args, ready)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(exc.stderr or str(exc))
        raise SystemExit(exc.returncode) from exc

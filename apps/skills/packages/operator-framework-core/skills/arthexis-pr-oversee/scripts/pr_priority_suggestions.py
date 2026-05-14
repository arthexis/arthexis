#!/usr/bin/env python3
"""List open PRs and suggest the top priorities to handle next."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

PR_FIELDS = ",".join(
    [
        "number",
        "title",
        "author",
        "baseRefName",
        "headRefName",
        "headRefOid",
        "createdAt",
        "updatedAt",
        "isDraft",
        "mergeStateStatus",
        "mergeable",
        "reviewDecision",
        "statusCheckRollup",
        "labels",
        "url",
    ]
)

GOOD_CHECKS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
BAD_CHECKS = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"}
PENDING_CHECKS = {"PENDING", "QUEUED", "IN_PROGRESS", "REQUESTED", "WAITING", "EXPECTED"}
BAD_MERGE_STATES = {"DIRTY", "BLOCKED", "BEHIND", "UNSTABLE"}


def require_gh() -> None:
    if not shutil.which("gh"):
        raise SystemExit("gh CLI not found on PATH")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def gh_json(args: list[str]) -> Any:
    require_gh()
    proc = run(["gh", *args])
    if not proc.stdout.strip():
        return None
    return json.loads(proc.stdout)


def split_repo(value: str) -> tuple[str, str]:
    try:
        owner, repo = value.split("/", 1)
    except ValueError as exc:
        raise SystemExit("--repo must use owner/name format") from exc
    if not owner or not repo:
        raise SystemExit("--repo must use owner/name format")
    return owner, repo


def author_login(pr: dict[str, Any]) -> str:
    author = pr.get("author") or {}
    if isinstance(author, dict):
        return str(author.get("login") or "")
    return str(author)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_days(value: str | None) -> float:
    parsed = parse_time(value)
    if parsed is None:
        return 0.0
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86400)


def list_open_prs(repo: str, limit: int) -> list[dict[str, Any]]:
    prs = gh_json(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            str(limit),
            "--json",
            PR_FIELDS,
        ]
    )
    return prs or []


def review_thread_counts(repo: str, number: int) -> dict[str, int]:
    owner, repo_name = split_repo(repo)
    query = """
query($owner:String!, $repo:String!, $number:Int!) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$number) {
      reviewThreads(first:100) {
        nodes {
          isResolved
          isOutdated
        }
      }
    }
  }
}
"""
    proc = run(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-f",
            f"owner={owner}",
            "-f",
            f"repo={repo_name}",
            "-F",
            f"number={number}",
        ],
        check=False,
    )
    if proc.returncode != 0:
        return {"unresolved": 0, "currentUnresolved": 0, "threadLookupFailed": 1}
    if not proc.stdout.strip():
        return {"unresolved": 0, "currentUnresolved": 0, "threadLookupFailed": 1}
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"unresolved": 0, "currentUnresolved": 0, "threadLookupFailed": 1}
    threads = (
        payload.get("data", {})
        .get("repository", {})
        .get("pullRequest", {})
        .get("reviewThreads", {})
        .get("nodes", [])
    )
    unresolved = [thread for thread in threads if not thread.get("isResolved")]
    current = [thread for thread in unresolved if not thread.get("isOutdated")]
    return {
        "unresolved": len(unresolved),
        "currentUnresolved": len(current),
        "threadLookupFailed": 0,
    }


def check_rollup_state(pr: dict[str, Any]) -> tuple[list[str], list[str]]:
    failing: list[str] = []
    pending: list[str] = []
    for check in pr.get("statusCheckRollup") or []:
        name = str(check.get("name") or check.get("workflowName") or check.get("context") or "check")
        conclusion = str(check.get("conclusion") or check.get("state") or "").upper()
        status = str(check.get("status") or "").upper()
        if conclusion in BAD_CHECKS:
            failing.append(f"{name}:{conclusion}")
        elif conclusion in PENDING_CHECKS:
            pending.append(f"{name}:{conclusion}")
        elif conclusion and conclusion not in GOOD_CHECKS:
            pending.append(f"{name}:{conclusion}")
        elif status and status not in {"COMPLETED", "SUCCESS"}:
            pending.append(f"{name}:{status}")
        elif not conclusion and not status:
            pending.append(f"{name}:UNKNOWN")
    return failing, pending


def command_for(repo: str, pr: dict[str, Any], action: str) -> str:
    number = int(pr["number"])
    if action == "merge":
        return (
            ".venv\\Scripts\\python.exe manage.py pr_oversee "
            f"--repo {repo} --json monitor --pr {number} "
            f"--expected-head-sha {pr.get('headRefOid')} --max-iterations 30 "
            "--interval 30 --merge --write --delete-branch"
        )
    if action == "review":
        return (
            ".venv\\Scripts\\python.exe manage.py pr_oversee "
            f"--repo {repo} --json comments --unresolved --pr {number}"
        )
    if action == "ci":
        return (
            ".venv\\Scripts\\python.exe manage.py pr_oversee "
            f"--repo {repo} --json ci --logs --pr {number}"
        )
    if action == "checkout":
        return f".venv\\Scripts\\python.exe manage.py pr_oversee --repo {repo} checkout --pr {number}"
    return (
        ".venv\\Scripts\\python.exe manage.py pr_oversee "
        f"--repo {repo} --json monitor --pr {number} --max-iterations 1 --interval 0"
    )


def summarize_pr(repo: str, pr: dict[str, Any], include_threads: bool) -> dict[str, Any]:
    failing, pending = check_rollup_state(pr)
    thread_counts = (
        review_thread_counts(repo, int(pr["number"]))
        if include_threads
        else {"unresolved": 0, "currentUnresolved": 0, "threadLookupFailed": 0}
    )

    merge_state = str(pr.get("mergeStateStatus") or "").upper()
    review = str(pr.get("reviewDecision") or "").upper()
    stale_bonus = min(age_days(pr.get("updatedAt")), 14.0) * 0.5

    if pr.get("isDraft"):
        action = "monitor"
        score = 10.0 + stale_bonus
        reason = "draft PR is not ready for active merge work"
    elif thread_counts["currentUnresolved"]:
        action = "review"
        score = 90.0 + min(thread_counts["currentUnresolved"] * 5.0, 20.0) + stale_bonus
        reason = f"{thread_counts['currentUnresolved']} current unresolved review thread(s)"
    elif thread_counts["unresolved"]:
        action = "review"
        score = 82.0 + min(thread_counts["unresolved"] * 3.0, 12.0) + stale_bonus
        reason = f"{thread_counts['unresolved']} unresolved review thread(s)"
    elif thread_counts["threadLookupFailed"]:
        action = "review"
        score = 78.0 + stale_bonus
        reason = "review thread lookup failed"
    elif merge_state in BAD_MERGE_STATES:
        action = "checkout"
        score = 80.0 + stale_bonus
        reason = f"merge state is {merge_state}"
    elif failing:
        action = "ci"
        score = 75.0 + min(len(failing) * 4.0, 16.0) + stale_bonus
        reason = f"{len(failing)} failing check(s)"
    elif pending:
        action = "monitor"
        score = 55.0 + stale_bonus
        reason = f"{len(pending)} pending check(s)"
    elif review in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
        action = "review"
        score = 52.0 + stale_bonus
        reason = f"review decision is {review}"
    else:
        action = "merge"
        score = 100.0 + stale_bonus
        reason = "no detected blockers; verify gate and merge"

    summary = {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "author": author_login(pr),
        "url": pr.get("url"),
        "headRefName": pr.get("headRefName"),
        "headRefOid": pr.get("headRefOid"),
        "updatedAt": pr.get("updatedAt"),
        "isDraft": pr.get("isDraft"),
        "mergeStateStatus": pr.get("mergeStateStatus"),
        "mergeable": pr.get("mergeable"),
        "reviewDecision": pr.get("reviewDecision"),
        "unresolvedReviewThreads": thread_counts["unresolved"],
        "currentUnresolvedReviewThreads": thread_counts["currentUnresolved"],
        "threadLookupFailed": bool(thread_counts["threadLookupFailed"]),
        "failingChecks": failing,
        "pendingChecks": pending,
        "priorityScore": round(score, 2),
        "suggestedAction": action,
        "priorityReason": reason,
    }
    summary["suggestedCommand"] = command_for(repo, summary, action)
    return summary


def print_report(open_prs: list[dict[str, Any]], top: list[dict[str, Any]]) -> None:
    print(f"Top {len(top)} priority suggestions")
    for index, pr in enumerate(top, 1):
        print(f"{index}. #{pr['number']} {pr['suggestedAction']} score={pr['priorityScore']}")
        print(f"   {pr['title']}")
        print(f"   reason={pr['priorityReason']}")
        print(f"   command={pr['suggestedCommand']}")
        print(f"   {pr['url']}")
    print()
    print(f"All open PRs ({len(open_prs)})")
    for pr in open_prs:
        print(
            f"#{pr['number']} score={pr['priorityScore']} action={pr['suggestedAction']} "
            f"threads={pr['currentUnresolvedReviewThreads']}/{pr['unresolvedReviewThreads']} "
            f"failing={len(pr['failingChecks'])} pending={len(pr['pendingChecks'])} "
            f"draft={pr['isDraft']} updated={pr['updatedAt']} {pr['title']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="arthexis/arthexis", help="GitHub repo as owner/name.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum open PRs to inspect.")
    parser.add_argument("--top", type=int, default=3, help="Number of priority suggestions to return.")
    parser.add_argument(
        "--no-review-threads",
        action="store_true",
        help="Skip per-PR review-thread lookups for faster but less precise ranking.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    raw_prs = list_open_prs(args.repo, args.limit)
    open_prs = [
        summarize_pr(args.repo, pr, include_threads=not args.no_review_threads)
        for pr in raw_prs
    ]
    ranked = sorted(
        open_prs,
        key=lambda item: (
            float(item["priorityScore"]),
            age_days(str(item.get("updatedAt") or "")),
            int(item["number"]),
        ),
        reverse=True,
    )
    top = ranked[: max(0, args.top)]
    output = {"repo": args.repo, "openPullRequests": open_prs, "topSuggestions": top}
    if args.json:
        print(json.dumps(output, indent=2))
    else:
        print_report(open_prs, top)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(exc.stderr or str(exc))
        raise SystemExit(exc.returncode) from exc

"""PullRequestOverseer orchestration and command runner integration."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .affinity import infer_work_profile, score_node_affinity
from .checks import (
    _author_login,
    check_rollup_state,
    dependency_duplicates,
    readiness_gate,
)
from .hygiene import changed_files_to_test_plan, hygiene_report
from .reply import review_reply_summary
from .types import (
    CommandResult,
    JSONValue,
    PullRequestOverseeError,
    _coerce_list,
    _coerce_mapping,
    _json_loads,
)
from .worktree import (
    PATCHWORK_METADATA,
    PATCHWORK_OWNED_NOISE,
    _git_worktree_missing_error,
    _is_reparse_point,
    _local_venv_link,
    _path_is_relative_to,
    _remove_owned_path,
    _restore_owned_venv_link,
    _status_is_patchwork_noise,
    _unlink_owned_venv_link,
    default_patchwork_dir,
)

PR_FIELDS = ",".join(
    [
        "number",
        "title",
        "author",
        "body",
        "baseRefName",
        "commits",
        "files",
        "headRefName",
        "headRefOid",
        "isDraft",
        "mergeStateStatus",
        "mergeable",
        "reviewDecision",
        "state",
        "statusCheckRollup",
        "updatedAt",
        "url",
    ]
)

REVIEW_SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "unrated": 4}
PRIORITY_DOMAIN_RANKS = {"ocpp": 0, "imager": 1}
DEFAULT_DOMAIN_PRIORITY = 2
SONAR_ADVISORY_CHECK_LABELS = {"sonarcloud", "sonarcloudcodeanalysis"}
CHECK_RUN_PAGE_SIZE = 100
PR_REFERENCE_PATTERN = re.compile(
    r"^(?:(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+))?#(?P<number>[1-9][0-9]*)$"
)

DOMAIN_PREFLIGHT_RULES: tuple[
    tuple[str, tuple[str, ...], tuple[str, ...]],
    ...,
] = (
    (
        "RFID command/card lifecycle",
        ("apps/cards/", "rfid", "command card", "card lifecycle"),
        (
            "Card removal resets held-card presence before execution.",
            "Execution is idempotent when the same card is scanned twice.",
            "Lifecycle mode is explicit and cannot leak between users/cards.",
        ),
    ),
    (
        "GWAY number reservation",
        ("gway", "reservation", "reserve", "claim", "node number"),
        (
            "Duplicate reservations are rejected or converge to the same claim.",
            "Reservation claims require an authenticated or unguessable token.",
            "Remote reservation fallback cannot silently allocate conflicting numbers.",
        ),
    ),
    (
        "Image burn/bootstrap",
        ("apps/imager/", "burn", "image", "bootstrap", "base image"),
        (
            "Base image inputs are not mutated during template generation.",
            "Burn scripts fail closed when required tokens, node IDs, or routes are missing.",
            "Generated artifacts are deterministic and kept out of unrelated commits.",
        ),
    ),
    (
        "Node registration",
        ("apps/nodes/", "registration", "enrollment", "downstream", "suite id"),
        (
            "Registration validates the claiming node before downstream writes.",
            "Enrollment or claim tokens have a narrow scope and clear expiry path.",
            "Re-registration does not orphan previous node state.",
        ),
    ),
    (
        "Network/AP portal",
        ("ap_portal", "ap portal gateway", "nmcli", "wifi", "route", "ssh"),
        (
            "Route, SSH, and HTTP checks are explicit and report actionable failures.",
            "AP portal changes preserve recovery access to the node.",
            "Network tests do not assume Windows paths or host-only tools.",
        ),
    ),
)


def _parse_github_timestamp(value: str) -> datetime | None:
    """Parse a GitHub ISO timestamp into an aware UTC datetime."""

    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_github_timestamp(value: datetime) -> str:
    """Return a compact UTC timestamp using GitHub's trailing-Z style."""

    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _compact_text(value: str, *, width: int = 180) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= width:
        return text
    return text[: width - 1].rstrip() + "..."


def parse_pr_dependency_edges(
    values: Iterable[str], *, default_repo: str
) -> dict[str, tuple[str, ...]]:
    """Parse ``dependent=prerequisite[,prerequisite]`` graph edges."""

    def reference(value: str) -> str:
        match = PR_REFERENCE_PATTERN.fullmatch(value.strip())
        if not match:
            raise PullRequestOverseeError(
                "PR dependency references must use #123 or owner/repo#123"
            )
        repo = match.group("repo") or default_repo
        return f"{repo}#{int(match.group('number'))}"

    edges: dict[str, tuple[str, ...]] = {}
    for raw_value in values:
        dependent_value, separator, prerequisites_value = str(raw_value).partition("=")
        if not separator or not dependent_value.strip() or not prerequisites_value.strip():
            raise PullRequestOverseeError(
                "PR dependency edges must use dependent=prerequisite[,prerequisite]"
            )
        dependent = reference(dependent_value)
        prerequisites = tuple(
            dict.fromkeys(
                reference(value)
                for value in prerequisites_value.split(",")
                if value.strip()
            )
        )
        if not prerequisites:
            raise PullRequestOverseeError(
                "PR dependency edges need at least one prerequisite"
            )
        if dependent in prerequisites:
            raise PullRequestOverseeError(
                f"PR dependency edge cannot depend on itself: {dependent}"
            )
        if dependent in edges:
            raise PullRequestOverseeError(
                f"PR dependency edge is duplicated: {dependent}"
            )
        edges[dependent] = prerequisites
    if not edges:
        raise PullRequestOverseeError("At least one --dependency edge is required")
    return edges


def _dependency_order(edges: Mapping[str, Iterable[str]]) -> tuple[list[str], list[str]]:
    """Return prerequisites-first order and any nodes participating in a cycle."""

    prerequisites = {node: set(values) for node, values in edges.items()}
    graph = {node: set(values) for node, values in prerequisites.items()}
    all_nodes = set(prerequisites)
    all_nodes.update(node for values in prerequisites.values() for node in values)
    for node in all_nodes:
        prerequisites.setdefault(node, set())
        graph.setdefault(node, set())

    order: list[str] = []
    available = sorted(node for node, values in prerequisites.items() if not values)
    while available:
        node = available.pop(0)
        order.append(node)
        for dependent in sorted(prerequisites):
            if node not in prerequisites[dependent]:
                continue
            prerequisites[dependent].remove(node)
            if not prerequisites[dependent] and dependent not in order:
                available.append(dependent)
                available.sort()
    cycle_members: set[str] = set()
    visited: set[str] = set()
    visiting: set[str] = set()
    path: list[str] = []

    def find_cycles(node: str) -> None:
        visited.add(node)
        visiting.add(node)
        path.append(node)
        for prerequisite in graph[node]:
            if prerequisite in visiting:
                cycle_members.update(path[path.index(prerequisite) :])
            elif prerequisite not in visited:
                find_cycles(prerequisite)
        path.pop()
        visiting.remove(node)

    for node in sorted(graph):
        if node not in visited:
            find_cycles(node)
    return order, sorted(cycle_members)


def _detect_review_severity(body: str) -> str:
    match = re.search(r"(?:^|[^A-Za-z0-9])P([0-3])(?:[^A-Za-z0-9]|$)", body)
    if match:
        return f"P{match.group(1)}"
    lowered = body.casefold()
    if "security" in lowered or "unauthenticated" in lowered:
        return "P1"
    if "bug" in lowered or "broken" in lowered or "incorrect" in lowered:
        return "P2"
    return "unrated"


def _review_severity_rank(severity: str) -> int:
    return REVIEW_SEVERITY_RANK.get(severity, len(REVIEW_SEVERITY_RANK))


def _priority_domain_rank(priority_domains: Iterable[str]) -> int:
    ranks = [
        PRIORITY_DOMAIN_RANKS.get(str(domain).casefold(), DEFAULT_DOMAIN_PRIORITY)
        for domain in priority_domains
        if str(domain).strip()
    ]
    return min(ranks, default=DEFAULT_DOMAIN_PRIORITY)


def _check_name(check: Mapping[str, Any]) -> str:
    return str(
        check.get("name") or check.get("workflowName") or check.get("context") or ""
    )


def _check_state(check: Mapping[str, Any]) -> str:
    return str(
        check.get("conclusion") or check.get("state") or check.get("status") or ""
    ).upper()


def _normalized_check_label(value: object) -> str:
    return "".join(
        character for character in str(value).casefold() if character.isalnum()
    )


def _needs_check_run_app_identity(check: Mapping[str, Any]) -> bool:
    return (
        str(check.get("__typename") or "") == "CheckRun"
        and "app" not in check
        and _normalized_check_label(_check_name(check)) in SONAR_ADVISORY_CHECK_LABELS
    )


def _rollup_check_run_match_key(check: Mapping[str, Any]) -> tuple[str, str] | None:
    name = str(check.get("name") or "")
    details_url = str(check.get("detailsUrl") or "")
    if not name or not details_url:
        return None
    return name, details_url


def _api_check_run_match_key(check: Mapping[str, Any]) -> tuple[str, str] | None:
    name = str(check.get("name") or "")
    details_url = str(check.get("details_url") or check.get("detailsUrl") or "")
    if not name or not details_url:
        return None
    return name, details_url


class CommandRunner:
    """Small command runner wrapper for GitHub and Git commands."""

    def run(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        check: bool = False,
    ) -> CommandResult:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        result = CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
        if check and result.returncode != 0:
            message = (
                result.stderr.strip() or result.stdout.strip() or f"{command[0]} failed"
            )
            raise PullRequestOverseeError(message)
        return result


class PullRequestOverseer:
    """Command-backed deterministic PR oversight surface."""

    def __init__(
        self,
        *,
        repo: str,
        runner: CommandRunner | None = None,
        cwd: Path | None = None,
        sleep_func: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.repo = repo
        self.runner = runner or CommandRunner()
        self.cwd = cwd or Path.cwd()
        self._sleep = sleep_func or time.sleep
        self._clock = clock or time.monotonic

    def gh_json(self, args: list[str]) -> JSONValue:
        result = self.runner.run(["gh", *args], cwd=self.cwd, check=True)
        return _json_loads(result.stdout)

    def gh_text(self, args: list[str]) -> str:
        result = self.runner.run(["gh", *args], cwd=self.cwd, check=True)
        return result.stdout.strip()

    def git(self, args: list[str]) -> str:
        result = self.runner.run(["git", *args], cwd=self.cwd, check=True)
        return result.stdout.strip()

    def pr_view(self, number: int) -> dict[str, Any]:
        payload = self.gh_json(
            ["pr", "view", str(number), "--repo", self.repo, "--json", PR_FIELDS]
        )
        pr = _coerce_mapping(payload)
        if not pr.get("baseRefOid"):
            pr["baseRefOid"] = self._local_base_ref_oid(
                str(pr.get("baseRefName") or "")
            )
        self._enrich_status_check_rollup_app_identities(pr)
        return pr

    def _local_base_ref_oid(self, base_ref_name: str) -> str:
        """Return the local origin SHA for a PR base branch when available."""

        if not base_ref_name:
            return ""
        result = self.runner.run(
            [
                "git",
                "rev-parse",
                "--verify",
                f"refs/remotes/origin/{base_ref_name}^{{commit}}",
            ],
            cwd=self.cwd,
            check=False,
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def list_open_prs(self, limit: int = 80) -> list[dict[str, Any]]:
        payload = self.gh_json(
            [
                "pr",
                "list",
                "--repo",
                self.repo,
                "--state",
                "open",
                "--limit",
                str(limit),
                "--json",
                "number,title,author,headRefName,headRefOid,baseRefName,isDraft,mergeStateStatus,reviewDecision,statusCheckRollup,url,updatedAt",
            ]
        )
        items = [_coerce_mapping(item) for item in _coerce_list(payload)]
        for item in items:
            self._enrich_status_check_rollup_app_identities(item)
        return items

    def _enrich_status_check_rollup_app_identities(
        self, pr: dict[str, Any], *, repo: str | None = None
    ) -> None:
        checks = [
            check
            for check in (
                _coerce_mapping(item)
                for item in _coerce_list(pr.get("statusCheckRollup"))
            )
            if _needs_check_run_app_identity(check)
        ]
        if not checks:
            return
        head_sha = str(pr.get("headRefOid") or "")
        if not head_sha:
            return
        tracked_keys = {
            key
            for key in (_rollup_check_run_match_key(check) for check in checks)
            if key is not None
        }
        if not tracked_keys:
            return
        apps_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        ambiguous_keys: set[tuple[str, str]] = set()
        seen_keys: set[tuple[str, str]] = set()
        page = 1
        target_repo = repo or self.repo
        while True:
            try:
                payload = self.gh_json(
                    [
                        "api",
                        (
                            f"repos/{target_repo}/commits/{head_sha}/check-runs?"
                            f"per_page={CHECK_RUN_PAGE_SIZE}&page={page}&filter=all"
                        ),
                    ]
                )
            except PullRequestOverseeError:
                return
            page_runs = _coerce_list(_coerce_mapping(payload).get("check_runs"))
            for raw_run in page_runs:
                run = _coerce_mapping(raw_run)
                key = _api_check_run_match_key(run)
                if key is None or key not in tracked_keys:
                    continue
                if key in seen_keys:
                    ambiguous_keys.add(key)
                    apps_by_key.pop(key, None)
                    continue
                seen_keys.add(key)
                app = _coerce_mapping(run.get("app"))
                if app:
                    apps_by_key[key] = app
            if len(page_runs) < CHECK_RUN_PAGE_SIZE:
                break
            page += 1
        for check in checks:
            key = _rollup_check_run_match_key(check)
            if key is not None and key not in ambiguous_keys and key in apps_by_key:
                check["app"] = apps_by_key[key]

    def closed_pr_report(
        self,
        *,
        since_hours: float = 8.0,
        limit: int = 100,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return PRs closed within the requested recent time window."""

        if since_hours <= 0:
            raise PullRequestOverseeError("since_hours must be greater than zero")
        if limit <= 0:
            raise PullRequestOverseeError("limit must be greater than zero")

        reference_time = now or datetime.now(timezone.utc)
        if reference_time.tzinfo is None:
            reference_time = reference_time.replace(tzinfo=timezone.utc)
        cutoff = reference_time.astimezone(timezone.utc) - timedelta(hours=since_hours)
        payload = self.gh_json(
            [
                "pr",
                "list",
                "--repo",
                self.repo,
                "--state",
                "all",
                "--limit",
                str(limit),
                "--json",
                "number,title,url,state,author,closedAt,mergedAt,headRefName,baseRefName",
            ]
        )
        items: list[dict[str, Any]] = []
        for raw_item in _coerce_list(payload):
            item = _coerce_mapping(raw_item)
            closed_at = _parse_github_timestamp(str(item.get("closedAt") or ""))
            if closed_at is None or closed_at < cutoff:
                continue
            author = _coerce_mapping(item.get("author"))
            normalized = {
                "number": item.get("number"),
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "state": str(item.get("state") or "").upper(),
                "author": str(author.get("login") or ""),
                "closedAt": str(item.get("closedAt") or ""),
                "mergedAt": str(item.get("mergedAt") or ""),
                "headRefName": str(item.get("headRefName") or ""),
                "baseRefName": str(item.get("baseRefName") or ""),
            }
            items.append(normalized)

        items.sort(key=lambda row: str(row.get("closedAt") or ""), reverse=True)
        return {
            "repo": self.repo,
            "sinceHours": since_hours,
            "cutoff": _format_github_timestamp(cutoff),
            "closedCount": len(items),
            "mergedCount": sum(1 for item in items if item["state"] == "MERGED"),
            "closedUnmergedCount": sum(
                1 for item in items if item["state"] != "MERGED"
            ),
            "items": items,
        }

    def pull_request_state_lookup(self, numbers: Iterable[int]) -> dict[int, str]:
        """Return PR states for a set of PR numbers using a batched list call."""

        wanted = sorted({number for number in numbers if number})
        if not wanted:
            return {}
        payload = self.gh_json(
            [
                "pr",
                "list",
                "--repo",
                self.repo,
                "--state",
                "all",
                "--limit",
                str(max(100, len(wanted))),
                "--json",
                "number,state",
            ]
        )
        lookup: dict[int, str] = {}
        for item in _coerce_list(payload):
            row = _coerce_mapping(item)
            try:
                number = int(row.get("number") or 0)
            except (TypeError, ValueError):
                continue
            if number in wanted:
                lookup[number] = str(row.get("state") or "").upper()
        return lookup

    def comments(
        self,
        number: int,
        *,
        unresolved_only: bool = False,
        repo: str | None = None,
    ) -> dict[str, Any]:
        owner, name = (repo or self.repo).split("/", 1)
        query = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          startLine
          comments(first: 50) {
            nodes {
              id
              author { login }
              body
              createdAt
              updatedAt
              url
              path
              line
            }
          }
        }
        pageInfo {
          hasNextPage
          endCursor
        }
      }
    }
  }
}
""".strip()
        threads: list[Any] = []
        after = ""
        while True:
            command = [
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={name}",
                "-F",
                f"number={number}",
            ]
            if after:
                command.extend(["-F", f"after={after}"])
            payload = self.gh_json(command)
            data = _coerce_mapping(payload)
            pr = _coerce_mapping(
                _coerce_mapping(
                    _coerce_mapping(data.get("data")).get("repository")
                ).get("pullRequest")
            )
            review_threads = _coerce_mapping(pr.get("reviewThreads"))
            threads.extend(_coerce_list(review_threads.get("nodes")))
            page_info = _coerce_mapping(review_threads.get("pageInfo"))
            if not page_info.get("hasNextPage"):
                break
            next_cursor = str(page_info.get("endCursor") or "")
            if not next_cursor or next_cursor == after:
                break
            after = next_cursor
        normalized: list[dict[str, Any]] = []
        for raw_thread in threads:
            thread = _coerce_mapping(raw_thread)
            is_resolved = bool(thread.get("isResolved"))
            if unresolved_only and is_resolved:
                continue
            comments = []
            for raw_comment in _coerce_list(
                _coerce_mapping(thread.get("comments")).get("nodes")
            ):
                comment = _coerce_mapping(raw_comment)
                comments.append(
                    {
                        "id": str(comment.get("id") or ""),
                        "author": str(
                            _coerce_mapping(comment.get("author")).get("login") or ""
                        ),
                        "body": str(comment.get("body") or ""),
                        "createdAt": str(comment.get("createdAt") or ""),
                        "updatedAt": str(comment.get("updatedAt") or ""),
                        "url": str(comment.get("url") or ""),
                        "path": str(comment.get("path") or thread.get("path") or ""),
                        "line": comment.get("line") or thread.get("line"),
                    }
                )
            normalized.append(
                {
                    "id": str(thread.get("id") or ""),
                    "isResolved": is_resolved,
                    "isOutdated": bool(thread.get("isOutdated")),
                    "path": str(thread.get("path") or ""),
                    "line": thread.get("line"),
                    "startLine": thread.get("startLine"),
                    "comments": comments,
                }
            )
        return {
            "number": number,
            "threads": normalized,
            "unresolvedCount": sum(
                1 for thread in normalized if not thread["isResolved"]
            ),
        }

    def review_batch(
        self, number: int, *, include_resolved: bool = False
    ) -> dict[str, Any]:
        """Summarize review threads into a stable patch batch order."""

        pr = self.pr_view(number)
        review_threads = self.comments(number, unresolved_only=not include_resolved)
        threads: list[dict[str, Any]] = []
        severity_counts: dict[str, int] = {}
        for raw_thread in _coerce_list(review_threads.get("threads")):
            thread = _coerce_mapping(raw_thread)
            comments = _coerce_list(thread.get("comments"))
            first_comment = _coerce_mapping(comments[0]) if comments else {}
            latest_comment = _coerce_mapping(comments[-1]) if comments else {}
            body = str(first_comment.get("body") or "")
            severity = _detect_review_severity(body)
            if not thread.get("isResolved"):
                severity_counts[severity] = severity_counts.get(severity, 0) + 1
            threads.append(
                {
                    "id": thread.get("id"),
                    "path": thread.get("path") or "(no path)",
                    "line": thread.get("line") or thread.get("startLine"),
                    "isResolved": bool(thread.get("isResolved")),
                    "isOutdated": bool(thread.get("isOutdated")),
                    "severity": severity,
                    "author": str(
                        _coerce_mapping(first_comment.get("author")).get("login")
                        or first_comment.get("author")
                        or ""
                    ),
                    "createdAt": str(first_comment.get("createdAt") or ""),
                    "url": str(
                        latest_comment.get("url") or first_comment.get("url") or ""
                    ),
                    "summary": _compact_text(body),
                }
            )
        threads.sort(
            key=lambda item: (
                bool(item["isResolved"]),
                _review_severity_rank(str(item["severity"])),
                str(item["path"]),
                int(item["line"] or 0),
                str(item["createdAt"]),
            )
        )
        return {
            "repo": self.repo,
            "number": number,
            "title": pr.get("title"),
            "url": pr.get("url"),
            "state": pr.get("state"),
            "isDraft": bool(pr.get("isDraft")),
            "headRefName": pr.get("headRefName"),
            "headRefOid": pr.get("headRefOid"),
            "baseRefName": pr.get("baseRefName"),
            "unresolvedCount": review_threads.get("unresolvedCount"),
            "severityCounts": {
                key: severity_counts[key]
                for key in sorted(severity_counts, key=_review_severity_rank)
            },
            "threads": threads,
        }

    def domain_preflight(self, number: int) -> dict[str, Any]:
        """Return a domain-specific PR checklist for high-delay failure modes."""

        pr = self.pr_view(number)
        files = self._changed_file_paths_from_pr(pr) or self.changed_files(number)
        haystack = "\n".join(
            [str(pr.get("title") or ""), str(pr.get("body") or ""), *files]
        ).casefold()
        matches: list[dict[str, Any]] = []
        for name, needles, checklist in DOMAIN_PREFLIGHT_RULES:
            domain_files = [
                path
                for path in files
                if any(needle.casefold() in path.casefold() for needle in needles)
            ]
            if domain_files or any(needle.casefold() in haystack for needle in needles):
                matches.append(
                    {
                        "name": name,
                        "files": domain_files,
                        "checklist": list(checklist),
                    }
                )

        touched_paths = "\n".join(files)
        validation = [
            f".venv/bin/python manage.py pr_oversee --repo {self.repo} inspect --pr {number}",
            (
                f".venv/bin/python manage.py pr_oversee --repo {self.repo} "
                f"comments --unresolved --pr {number}"
            ),
            f".venv/bin/python manage.py pr_oversee --repo {self.repo} hygiene --pr {number}",
            f".venv/bin/python manage.py pr_oversee --repo {self.repo} test-plan --pr {number}",
        ]
        touched_lower = touched_paths.casefold()
        if "apps/cards/" in touched_paths or "rfid" in touched_lower:
            validation.append(".venv/bin/python -m pytest apps/cards")
        if "apps/imager/" in touched_paths:
            validation.append(".venv/bin/python -m pytest apps/imager")
        if "apps/nodes/" in touched_paths:
            validation.append(".venv/bin/python -m pytest apps/nodes")
        if "apps/nmcli/" in touched_paths or "ap_portal" in touched_lower:
            validation.append(".venv/bin/python -m pytest apps/nmcli")

        return {
            "repo": self.repo,
            "number": number,
            "title": pr.get("title"),
            "url": pr.get("url"),
            "state": pr.get("state"),
            "isDraft": bool(pr.get("isDraft")),
            "headRefName": pr.get("headRefName"),
            "headRefOid": pr.get("headRefOid"),
            "risk": self._domain_preflight_risk(matches),
            "matches": matches,
            "validationCommands": validation,
        }

    def _domain_preflight_risk(self, matches: Iterable[Mapping[str, Any]]) -> str:
        names = {str(match.get("name") or "") for match in matches}
        if {
            "GWAY number reservation",
            "Image burn/bootstrap",
            "Node registration",
        }.issubset(names):
            return "high"
        if len(names) >= 2:
            return "medium"
        if names:
            return "focused"
        return "low"

    def inspect(
        self,
        number: int,
        *,
        require_approval: bool = False,
        allow_pending: bool = False,
    ) -> dict[str, Any]:
        pr = self.pr_view(number)
        review_threads = self.comments(number, unresolved_only=False)
        pr["reviewThreads"] = review_threads["threads"]
        pr["unresolvedReviewThreadCount"] = review_threads["unresolvedCount"]
        return {
            "pullRequest": pr,
            "readiness": readiness_gate(
                pr,
                require_approval=require_approval,
                allow_pending=allow_pending,
            ),
        }

    def gate(
        self,
        number: int,
        *,
        require_approval: bool = False,
        allow_pending: bool = False,
    ) -> dict[str, Any]:
        return self.inspect(
            number,
            require_approval=require_approval,
            allow_pending=allow_pending,
        )["readiness"]

    def changed_files(self, number: int) -> list[str]:
        output = self.gh_text(
            ["pr", "diff", str(number), "--repo", self.repo, "--name-only"]
        )
        return [line.strip() for line in output.splitlines() if line.strip()]

    def local_changed_files(self, base_ref: str = "origin/main") -> list[str]:
        """Return changed files for the local checkout against a base ref."""

        cleaned = base_ref.strip() or "origin/main"
        paths: set[str] = set()

        def add_paths(output: str) -> None:
            for line in output.splitlines():
                path = line.strip()
                if not path:
                    continue
                if path in PATCHWORK_OWNED_NOISE or any(
                    path.startswith(f"{noise}/") for noise in PATCHWORK_OWNED_NOISE
                ):
                    continue
                paths.add(path)

        base_result = self.runner.run(
            ["git", "diff", "--name-only", f"{cleaned}...HEAD"],
            cwd=self.cwd,
            check=False,
        )
        if base_result.returncode == 0:
            add_paths(base_result.stdout)
        elif cleaned == "origin/main":
            fallback = self.runner.run(
                ["git", "diff", "--name-only", "main...HEAD"],
                cwd=self.cwd,
                check=False,
            )
            if fallback.returncode == 0:
                add_paths(fallback.stdout)
            else:
                message = (
                    fallback.stderr.strip()
                    or fallback.stdout.strip()
                    or base_result.stderr.strip()
                    or base_result.stdout.strip()
                    or "git diff failed for origin/main and main"
                )
                raise PullRequestOverseeError(message)
        else:
            message = (
                base_result.stderr.strip()
                or base_result.stdout.strip()
                or f"git diff failed for {cleaned}"
            )
            raise PullRequestOverseeError(message)
        for command in (
            ["git", "diff", "--name-only"],
            ["git", "diff", "--cached", "--name-only"],
            ["git", "ls-files", "--others", "--exclude-standard"],
        ):
            result = self.runner.run(command, cwd=self.cwd, check=True)
            add_paths(result.stdout)
        return sorted(paths)

    def test_plan_for_files(
        self, files: Iterable[str], *, source: str = "changed-files"
    ) -> dict[str, Any]:
        plan = changed_files_to_test_plan(files)
        plan["source"] = source
        return plan

    def test_plan(self, number: int) -> dict[str, Any]:
        plan = changed_files_to_test_plan(self.changed_files(number))
        plan["source"] = "pull-request"
        plan["number"] = number
        return plan

    def local_test_plan(self, *, base_ref: str = "origin/main") -> dict[str, Any]:
        plan = changed_files_to_test_plan(self.local_changed_files(base_ref))
        plan["source"] = "local-diff"
        plan["baseRef"] = base_ref.strip() or "origin/main"
        return plan

    def ci_failures(
        self, number: int, *, include_logs: bool = False, log_limit: int = 4000
    ) -> dict[str, Any]:
        pr = self.pr_view(number)
        return self._ci_failures_from_pr(
            number, pr, include_logs=include_logs, log_limit=log_limit
        )

    def _ci_failures_from_pr(
        self,
        number: int,
        pr: Mapping[str, Any],
        *,
        include_logs: bool = False,
        log_limit: int = 4000,
    ) -> dict[str, Any]:
        checks = check_rollup_state(pr)
        failures = [*checks["failing"], *checks["pending"]]
        log_snippets: dict[str, str] = {}
        if include_logs:
            for failure in failures:
                details_url = failure.get("detailsUrl", "")
                match = re.search(r"/actions/runs/(\d+)", details_url)
                if not match:
                    continue
                result = self.runner.run(
                    [
                        "gh",
                        "run",
                        "view",
                        match.group(1),
                        "--repo",
                        self.repo,
                        "--log-failed",
                    ],
                    cwd=self.cwd,
                    check=False,
                )
                if result.returncode == 0 and result.stdout:
                    log_snippets[failure["name"]] = result.stdout[:log_limit]
        return {
            "number": number,
            "failures": failures,
            "logs": log_snippets,
        }

    def run_validation_commands(
        self,
        commands: Iterable[Iterable[object]],
        *,
        output_limit: int = 4000,
        cwd: Path | None = None,
    ) -> dict[str, Any]:
        """Run generated local validation commands and summarize their results."""

        results: list[dict[str, Any]] = []
        execution_cwd = cwd or self.cwd
        for raw_command in commands:
            command = [str(part) for part in raw_command]
            result = self.runner.run(command, cwd=execution_cwd, check=False)
            results.append(
                {
                    "command": command,
                    "returncode": result.returncode,
                    "stdout": result.stdout[-output_limit:],
                    "stderr": result.stderr[-output_limit:],
                }
            )
        return {
            "ok": all(item["returncode"] == 0 for item in results),
            "cwd": str(execution_cwd),
            "commands": results,
        }

    def dependency_dedupe(self, *, limit: int = 80) -> dict[str, Any]:
        return dependency_duplicates(self.list_open_prs(limit=limit))

    def dependency_graph(
        self,
        *,
        dependencies: Iterable[str],
        require_approval: bool = False,
        allow_pending: bool = False,
    ) -> dict[str, Any]:
        """Report explicit cross-repository PR prerequisites without mutation."""

        edges = parse_pr_dependency_edges(dependencies, default_repo=self.repo)
        order, cycles = _dependency_order(edges)
        references = [
            *order,
            *cycles,
            *sorted(
                {
                    reference
                    for reference, prerequisites in edges.items()
                    for reference in (reference, *prerequisites)
                }
                - set(order)
                - set(cycles)
            ),
        ]
        entries: dict[str, dict[str, Any]] = {}
        for reference in references:
            repo, number_text = reference.rsplit("#", 1)
            result = self.runner.run(
                ["gh", "pr", "view", number_text, "--repo", repo, "--json", PR_FIELDS],
                cwd=self.cwd,
                check=False,
            )
            requirements = list(edges.get(reference, ()))
            if result.returncode != 0:
                entries[reference] = {
                    "pr": reference,
                    "repo": repo,
                    "number": int(number_text),
                    "requires": requirements,
                    "state": "UNKNOWN",
                    "error": result.stderr.strip()
                    or result.stdout.strip()
                    or "gh pr view failed",
                    "readyToMerge": False,
                }
                continue
            pr = _coerce_mapping(_json_loads(result.stdout))
            self._enrich_status_check_rollup_app_identities(pr, repo=repo)
            review_threads = self.comments(int(number_text), repo=repo)
            pr["unresolvedReviewThreadCount"] = review_threads["unresolvedCount"]
            gate = readiness_gate(
                pr,
                require_approval=require_approval,
                allow_pending=allow_pending,
            )
            entries[reference] = {
                "pr": reference,
                "repo": repo,
                "number": int(number_text),
                "title": pr.get("title"),
                "url": pr.get("url"),
                "requires": requirements,
                "state": str(pr.get("state") or "UNKNOWN").upper(),
                "readyToMerge": bool(gate["ready"]),
                "gate": gate,
            }

        next_actions: list[dict[str, str]] = []
        for reference in references:
            entry = entries[reference]
            blocked_by = [
                dependency
                for dependency in entry["requires"]
                if entries[dependency]["state"] != "MERGED"
            ]
            entry["blockedBy"] = blocked_by
            if reference in cycles:
                entry["status"] = "dependency-cycle"
            elif entry["state"] == "MERGED":
                entry["status"] = "merged"
            elif entry.get("error"):
                entry["status"] = "unavailable"
            elif entry["state"] != "OPEN":
                entry["status"] = "not-open"
            elif blocked_by:
                entry["status"] = "blocked-by-dependency"
            elif entry["readyToMerge"]:
                entry["status"] = "ready-to-merge"
                next_actions.append({"pr": reference, "action": "merge"})
            else:
                entry["status"] = "awaiting-pr-gate"
                next_actions.append({"pr": reference, "action": "resolve-pr-gate"})

        return {
            "repo": self.repo,
            "dependencies": [
                {"pr": reference, "requires": list(edges[reference])}
                for reference in sorted(edges)
            ],
            "order": order,
            "cycles": cycles,
            "items": [entries[reference] for reference in references],
            "nextActions": next_actions,
        }

    def advance(
        self,
        *,
        limit: int = 80,
        include_drafts: bool = False,
        require_approval: bool = False,
        allow_pending: bool = False,
        ready_drafts: bool = False,
        merge: bool = False,
        method: str = "squash",
        delete_branch: bool = False,
        admin: bool = False,
        write: bool = False,
    ) -> dict[str, Any]:
        """Summarize and optionally advance open PRs by deterministic gates."""

        selection = self.select_candidates(limit=limit, include_drafts=include_drafts)
        items: list[dict[str, Any]] = []
        action_plans: list[dict[str, Any]] = []
        for candidate in _coerce_list(selection.get("candidates")):
            assessment = self.assess_pr(
                int(_coerce_mapping(candidate).get("number") or 0),
                require_approval=require_approval,
                allow_pending=allow_pending,
                ready_drafts=ready_drafts,
                merge=merge,
                method=method,
                delete_branch=delete_branch,
                admin=admin,
                write=write,
            )
            items.append(_coerce_mapping(assessment.get("item")))
            action_plans.extend(_coerce_list(assessment.get("actions")))

        ordered = sorted(
            items,
            key=lambda item: (
                int(item["operatorPriority"]) if "operatorPriority" in item else 1,
                int(item["priority"]) if "priority" in item else 99,
                (
                    int(item["domainPriority"])
                    if item.get("domainPriority") is not None
                    else 99
                ),
                str(item.get("updatedAt") or ""),
                int(item.get("number") or 0),
            ),
        )
        ordered_numbers = {
            int(item.get("number") or 0): index for index, item in enumerate(ordered)
        }
        ordered_action_plans = sorted(
            action_plans,
            key=lambda action: (
                ordered_numbers.get(
                    int(_coerce_mapping(action).get("number") or 0),
                    len(ordered_numbers),
                ),
                int(_coerce_mapping(action).get("number") or 0),
            ),
        )
        return {
            "repo": self.repo,
            "limit": limit,
            "includeDrafts": include_drafts,
            "write": write,
            "openCount": len(_coerce_list(selection.get("summaries"))),
            "consideredCount": len(items),
            "skipped": _coerce_list(selection.get("skipped")),
            "topSuggestions": ordered[:3],
            "items": ordered,
            "actions": self.execute_actions(ordered_action_plans) if write else [],
        }

    def node_queue(
        self,
        *,
        limit: int = 80,
        include_drafts: bool = False,
        node_role: str = "",
        installed_apps: Iterable[str] = (),
        hardware_tags: Iterable[str] = (),
        local_development: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Rank open PRs by this node's role, app profile, and hardware tags."""

        installed_app_list = tuple(str(item) for item in installed_apps)
        hardware_tag_list = tuple(str(item) for item in hardware_tags)
        selection = self.select_candidates(limit=limit, include_drafts=include_drafts)
        items: list[dict[str, Any]] = []
        for candidate in _coerce_list(selection.get("candidates")):
            number = int(_coerce_mapping(candidate).get("number") or 0)
            if not number:
                continue
            pr = self.pr_view(number)
            files = self._changed_file_paths_from_pr(pr) or self.changed_files(number)
            profile = infer_work_profile(
                title=str(pr.get("title") or ""),
                body=str(pr.get("body") or ""),
                files=files,
            )
            affinity = score_node_affinity(
                profile,
                node_role=node_role,
                installed_apps=installed_app_list,
                hardware_tags=hardware_tag_list,
            )
            items.append(
                {
                    "number": number,
                    "title": pr.get("title"),
                    "url": pr.get("url"),
                    "headRefName": pr.get("headRefName"),
                    "headRefOid": pr.get("headRefOid"),
                    "isDraft": bool(pr.get("isDraft")),
                    "updatedAt": pr.get("updatedAt"),
                    "changedFiles": files,
                    "workProfile": profile,
                    "nodeAffinity": affinity,
                }
            )

        ordered = sorted(
            items,
            key=lambda item: (
                int(_coerce_mapping(item.get("nodeAffinity")).get("score") or 0),
                str(item.get("updatedAt") or ""),
                int(item.get("number") or 0),
            ),
            reverse=True,
        )
        return {
            "repo": self.repo,
            "limit": limit,
            "includeDrafts": include_drafts,
            "nodeContext": {
                "role": node_role,
                "installedApps": sorted(installed_app_list),
                "hardwareTags": sorted(hardware_tag_list),
                "localDevelopment": dict(local_development or {}),
            },
            "openCount": len(_coerce_list(selection.get("summaries"))),
            "consideredCount": len(items),
            "skipped": _coerce_list(selection.get("skipped")),
            "topSuggestions": ordered[:3],
            "items": ordered,
        }

    def _changed_file_paths_from_pr(self, pr: Mapping[str, Any]) -> list[str]:
        files: list[str] = []
        for raw_file in _coerce_list(pr.get("files")):
            file_payload = _coerce_mapping(raw_file)
            path = str(file_payload.get("path") or "").strip()
            if path:
                files.append(path)
        return files

    def select_candidates(self, *, limit: int, include_drafts: bool) -> dict[str, Any]:
        if limit <= 0:
            raise PullRequestOverseeError("limit must be positive")
        summaries = self.list_open_prs(limit=limit)
        candidates: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for summary in summaries:
            number = int(summary.get("number") or 0)
            if not number:
                continue
            if summary.get("isDraft") and not include_drafts:
                skipped.append(
                    {
                        "number": number,
                        "title": summary.get("title"),
                        "reason": "draft",
                    }
                )
                continue
            candidates.append({"number": number, "summary": summary})
        return {
            "summaries": summaries,
            "candidates": candidates,
            "skipped": skipped,
        }

    def assess_pr(
        self,
        number: int,
        *,
        require_approval: bool,
        allow_pending: bool,
        ready_drafts: bool,
        merge: bool,
        method: str,
        delete_branch: bool,
        admin: bool,
        write: bool,
    ) -> dict[str, Any]:
        inspection = self.inspect(
            number,
            require_approval=require_approval,
            allow_pending=allow_pending,
        )
        pr = inspection["pullRequest"]
        gate = inspection["readiness"]
        files = self.changed_files(number)
        hygiene = hygiene_report(pr, files)
        work_profile = infer_work_profile(
            title=str(pr.get("title") or ""),
            body=str(pr.get("body") or ""),
            files=files,
        )
        item = self._advance_item(pr, gate, hygiene, work_profile=work_profile)
        item["suggestedCommand"] = self._advance_suggested_command(
            number,
            gate=gate,
            ready_to_merge=bool(item["readyToMerge"]),
            can_mark_ready=bool(item["canMarkReady"]),
            blockers=[str(blocker) for blocker in item["blockers"]],
            require_approval=require_approval,
            allow_pending=allow_pending,
            delete_branch=delete_branch,
            admin=admin,
        )
        action_plan = self._advance_action_plan(
            item,
            gate=gate,
            ready_drafts=ready_drafts,
            merge=merge,
            method=method,
            delete_branch=delete_branch,
            require_approval=require_approval,
            allow_pending=allow_pending,
            admin=admin,
        )
        actions = []
        if action_plan:
            item["plannedAction"] = action_plan["commandText"]
            if write:
                actions.append(action_plan)
        return {"item": item, "actions": actions}

    def execute_actions(
        self, actions_list: Iterable[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for action_plan in actions_list:
            action = str(action_plan.get("action") or "")
            number = int(action_plan.get("number") or 0)
            try:
                if action == "mark-ready":
                    results.append(
                        {
                            "action": action,
                            "number": number,
                            "stdout": self.gh_text(
                                [str(part) for part in action_plan["command"]]
                            ),
                        }
                    )
                elif action == "merge":
                    results.append(
                        {
                            "action": action,
                            "number": number,
                            "result": self.merge(
                                number,
                                method=str(action_plan.get("method") or "squash"),
                                delete_branch=bool(action_plan.get("deleteBranch")),
                                require_approval=bool(
                                    action_plan.get("requireApproval")
                                ),
                                expected_head_sha=str(
                                    action_plan.get("expectedHeadSha") or ""
                                ),
                                allow_pending=bool(action_plan.get("allowPending")),
                                admin=bool(action_plan.get("admin")),
                            ),
                        }
                    )
                else:
                    results.append(
                        {
                            "action": action or "unknown",
                            "number": number,
                            "error": "unsupported-action",
                        }
                    )
            except PullRequestOverseeError as exc:
                results.append(
                    {
                        "action": action,
                        "number": number,
                        "error": str(exc),
                    }
                )
        return results

    def _advance_action_plan(
        self,
        item: Mapping[str, Any],
        *,
        gate: Mapping[str, Any],
        ready_drafts: bool,
        merge: bool,
        method: str,
        delete_branch: bool,
        require_approval: bool,
        allow_pending: bool,
        admin: bool,
    ) -> dict[str, Any] | None:
        number = int(item.get("number") or 0)
        if item.get("canMarkReady") and ready_drafts:
            command = ["pr", "ready", str(number), "--repo", self.repo]
            return {
                "action": "mark-ready",
                "number": number,
                "command": command,
                "commandText": self._quoted_command(["gh", *command]),
            }
        if item.get("readyToMerge") and merge:
            expected_head_sha = str(gate.get("headRefOid") or "")
            return {
                "action": "merge",
                "number": number,
                "method": method,
                "deleteBranch": delete_branch,
                "requireApproval": require_approval,
                "expectedHeadSha": expected_head_sha,
                "allowPending": allow_pending,
                "admin": admin,
                "commandText": self._merge_command_text(
                    number,
                    method=method,
                    expected_head_sha=expected_head_sha,
                    delete_branch=delete_branch,
                    admin=admin,
                ),
            }
        return None

    def _quoted_command(self, command: Iterable[object]) -> str:
        parts = [str(part) for part in command]
        if os.name == "nt":
            return subprocess.list2cmdline(parts)
        return shlex.join(parts)

    def _manage_pr_oversee_command(self, *args: object) -> str:
        return self._quoted_command(
            [sys.executable, "manage.py", "pr_oversee", "--repo", self.repo, *args]
        )

    def _merge_command_text(
        self,
        number: int,
        *,
        method: str,
        expected_head_sha: str,
        delete_branch: bool,
        admin: bool,
    ) -> str:
        command = ["gh", "pr", "merge", str(number), "--repo", self.repo, f"--{method}"]
        if expected_head_sha:
            command.extend(["--match-head-commit", expected_head_sha])
        if delete_branch:
            command.append("--delete-branch")
        if admin:
            command.append("--admin")
        return self._quoted_command(command)

    def _advance_item(
        self,
        pr: Mapping[str, Any],
        gate: Mapping[str, Any],
        hygiene: Mapping[str, Any],
        *,
        work_profile: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        number = int(pr.get("number") or 0)
        blockers = [str(item) for item in _coerce_list(gate.get("blockers"))]
        non_draft_blockers = [item for item in blockers if item != "draft"]
        is_draft = bool(pr.get("isDraft"))
        hygiene_ok = bool(hygiene.get("ok"))
        ready_to_merge = bool(gate.get("ready")) and hygiene_ok and not is_draft
        can_mark_ready = is_draft and not non_draft_blockers and hygiene_ok
        priority_domains = sorted(
            str(item)
            for item in _coerce_list((work_profile or {}).get("priorityDomains"))
            if str(item).strip()
        )
        domain_priority = _priority_domain_rank(priority_domains)
        priority = self._advance_priority(
            blockers=blockers,
            hygiene_ok=hygiene_ok,
            ready_to_merge=ready_to_merge,
            can_mark_ready=can_mark_ready,
            is_draft=is_draft,
        )
        return {
            "number": number,
            "title": pr.get("title"),
            "url": pr.get("url"),
            "author": _author_login(pr),
            "headRefName": pr.get("headRefName"),
            "headRefOid": pr.get("headRefOid"),
            "isDraft": is_draft,
            "updatedAt": pr.get("updatedAt"),
            "priority": priority,
            "operatorPriority": (0 if domain_priority == 0 and not is_draft else 1),
            "domainPriority": domain_priority,
            "priorityDomains": priority_domains,
            "status": self._advance_status(
                blockers=blockers,
                hygiene_ok=hygiene_ok,
                ready_to_merge=ready_to_merge,
                can_mark_ready=can_mark_ready,
            ),
            "readyToMerge": ready_to_merge,
            "canMarkReady": can_mark_ready,
            "blockers": blockers,
            "warnings": _coerce_list(gate.get("warnings")),
            "hygiene": hygiene,
        }

    def _advance_priority(
        self,
        *,
        blockers: list[str],
        hygiene_ok: bool,
        ready_to_merge: bool,
        can_mark_ready: bool,
        is_draft: bool,
    ) -> int:
        if ready_to_merge:
            return 0
        if can_mark_ready:
            return 1
        if any(
            blocker.startswith(("review:", "review_threads:")) for blocker in blockers
        ):
            return 2
        if any(blocker.startswith("check:") for blocker in blockers):
            return 3
        if any(blocker.startswith("pending:") for blocker in blockers):
            return 4
        if any(
            blocker.startswith(("merge_state:", "mergeable:")) for blocker in blockers
        ):
            return 5
        if is_draft:
            return 6
        if not hygiene_ok:
            return 7
        return 8

    def _advance_status(
        self,
        *,
        blockers: list[str],
        hygiene_ok: bool,
        ready_to_merge: bool,
        can_mark_ready: bool,
    ) -> str:
        if ready_to_merge:
            return "ready-to-merge"
        if can_mark_ready:
            return "draft-ready"
        if blockers:
            return "blocked"
        if not hygiene_ok:
            return "hygiene-failed"
        return "needs-review"

    def _advance_suggested_command(
        self,
        number: int,
        *,
        gate: Mapping[str, Any],
        ready_to_merge: bool,
        can_mark_ready: bool,
        blockers: list[str],
        require_approval: bool,
        allow_pending: bool,
        delete_branch: bool,
        admin: bool,
    ) -> str:
        if ready_to_merge:
            command = ["monitor", "--pr", str(number), "--merge", "--write"]
            if delete_branch:
                command.append("--delete-branch")
            if require_approval:
                command.append("--require-approval")
            if allow_pending:
                command.append("--allow-pending")
            if admin:
                command.append("--admin")
            if gate.get("headRefOid"):
                command.extend(["--expected-head-sha", str(gate.get("headRefOid"))])
            return self._manage_pr_oversee_command(*command)
        if can_mark_ready:
            return f"gh pr ready {number} --repo {self.repo}"
        if any(blocker.startswith(("check:", "pending:")) for blocker in blockers):
            return self._manage_pr_oversee_command(
                "ci", "--pr", number, "--failures", "--logs"
            )
        if any(
            blocker.startswith(("review:", "review_threads:")) for blocker in blockers
        ):
            return self._manage_pr_oversee_command(
                "comments", "--pr", number, "--unresolved"
            )
        return self._manage_pr_oversee_command("inspect", "--pr", number)

    def checkout(
        self,
        number: int,
        *,
        worktree: Path,
        branch: str = "",
        link_venv: bool = True,
    ) -> dict[str, Any]:
        if worktree.exists():
            raise PullRequestOverseeError(f"Worktree path already exists: {worktree}")
        worktree.parent.mkdir(parents=True, exist_ok=True)
        pr = self.pr_view(number)
        remote_ref = f"refs/remotes/origin/pr/{number}"
        self.git(["fetch", "origin", f"pull/{number}/head:{remote_ref}"])
        args = ["worktree", "add"]
        if branch:
            args.extend(["-b", branch])
        else:
            args.append("--detach")
        args.extend([str(worktree), remote_ref])
        self.git(args)
        metadata = {
            "number": number,
            "repo": self.repo,
            "headRefName": pr.get("headRefName"),
            "headRefOid": pr.get("headRefOid"),
            "baseRefName": pr.get("baseRefName"),
            "baseRefOid": pr.get("baseRefOid"),
            "worktree": str(worktree),
        }
        if link_venv:
            metadata["venv"] = _local_venv_link(self.cwd / ".venv", worktree / ".venv")
        metadata_path = worktree / PATCHWORK_METADATA
        try:
            with open(
                metadata_path,
                "x",
                encoding="utf-8",
                opener=lambda path, flags: os.open(
                    path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600
                ),
            ) as handle:
                handle.write(json.dumps(metadata, indent=2) + "\n")
        except OSError:
            metadata["metadataWriteError"] = True
        return metadata

    def _worktree_status_lines(self, worktree: Path) -> list[str]:
        result = self.runner.run(
            [
                "git",
                "-C",
                str(worktree),
                "status",
                "--porcelain",
                "--untracked-files=normal",
            ],
            cwd=self.cwd,
            check=False,
        )
        if result.returncode != 0:
            return []
        return [line for line in result.stdout.splitlines() if line.strip()]

    def _remove_worktree(
        self,
        worktree: Path,
        *,
        patchwork_root: Path | None = None,
    ) -> dict[str, Any]:
        metadata = self._read_patchwork_metadata(worktree)
        metadata_exists = bool(metadata) or (worktree / PATCHWORK_METADATA).exists()
        status_lines = self._worktree_status_lines(worktree)
        can_force = metadata_exists and _status_is_patchwork_noise(status_lines)
        if patchwork_root is not None:
            can_force = can_force and _path_is_relative_to(worktree, patchwork_root)
        action: dict[str, Any] = {
            "action": "remove-worktree",
            "path": str(worktree),
        }
        if status_lines and not can_force:
            action.update(
                {
                    "returncode": 1,
                    "stderr": "worktree has non-owned changes",
                    "forced": False,
                    "status": status_lines,
                    "blocked": True,
                }
            )
            return action

        pre_remove = _unlink_owned_venv_link(
            worktree,
            metadata=metadata,
            patchwork_root=patchwork_root,
        )
        if pre_remove.get("reason") == "metadata-not-restorable":
            action["preRemove"] = pre_remove
            action.update(
                {
                    "returncode": 1,
                    "stderr": "pre-remove .venv link cleanup blocked",
                    "blocked": True,
                }
            )
            return action
        if pre_remove.get("attempted"):
            action["preRemove"] = pre_remove
            if not pre_remove.get("removed"):
                action.update(
                    {
                        "returncode": 1,
                        "stderr": "pre-remove .venv link cleanup failed",
                        "blocked": True,
                    }
                )
                return action

        def restore_venv_link() -> None:
            if not (
                pre_remove.get("attempted")
                and pre_remove.get("removed")
                and worktree.exists()
            ):
                return
            restore = _restore_owned_venv_link(
                worktree,
                metadata=metadata,
                patchwork_root=patchwork_root,
            )
            if restore.get("attempted"):
                action["venvRestore"] = restore

        result = self.runner.run(
            ["git", "worktree", "remove", str(worktree)], cwd=self.cwd, check=False
        )
        action.update(
            {
                "returncode": result.returncode,
                "stderr": result.stderr.strip(),
            }
        )
        if result.returncode == 0:
            residue = self._remove_patchwork_residue(
                worktree, patchwork_root=patchwork_root
            )
            if residue.get("attempted"):
                action["residue"] = residue
            return action

        if not can_force:
            action["forced"] = False
            action["status"] = status_lines
            restore_venv_link()
            return action

        forced = self.runner.run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=self.cwd,
            check=False,
        )
        action.update(
            {
                "forced": True,
                "forceReturncode": forced.returncode,
                "forceStderr": forced.stderr.strip(),
            }
        )
        if forced.returncode == 0:
            residue = self._remove_patchwork_residue(
                worktree, patchwork_root=patchwork_root
            )
            if residue.get("attempted"):
                action["residue"] = residue
        elif _git_worktree_missing_error(result, forced):
            local_remove = self._remove_patchwork_residue(
                worktree, patchwork_root=patchwork_root
            )
            action["localRemove"] = local_remove
        restore_venv_link()
        return action

    def _remove_patchwork_residue(
        self,
        worktree: Path,
        *,
        patchwork_root: Path | None = None,
    ) -> dict[str, Any]:
        if not worktree.exists():
            return {"attempted": False, "reason": "missing"}
        if patchwork_root is not None and not _path_is_relative_to(
            worktree, patchwork_root
        ):
            return {"attempted": False, "reason": "outside-patchwork-root"}
        try:
            children = list(worktree.iterdir())
        except OSError as exc:
            return {
                "attempted": False,
                "reason": "list-failed",
                "error": str(exc),
            }
        metadata = self._read_patchwork_metadata(worktree)
        blocked_names = [
            child.name
            for child in children
            if not self._is_owned_residue_path(child, metadata)
        ]
        residue_names = sorted(child.name for child in children)
        if blocked_names:
            return {
                "attempted": False,
                "reason": "non-owned-residue",
                "paths": sorted(blocked_names),
            }
        try:
            for child in children:
                _remove_owned_path(child)
            worktree.rmdir()
        except OSError as exc:
            return {
                "attempted": True,
                "removed": False,
                "reason": "remove-failed",
                "error": str(exc),
                "paths": residue_names,
            }
        return {
            "attempted": True,
            "removed": not worktree.exists(),
            "paths": residue_names,
        }

    def _read_patchwork_metadata(self, worktree: Path) -> dict[str, Any]:
        try:
            return _coerce_mapping(
                json.loads((worktree / PATCHWORK_METADATA).read_text())
            )
        except (OSError, json.JSONDecodeError):
            return {}

    def _is_owned_residue_path(self, child: Path, metadata: Mapping[str, Any]) -> bool:
        if child.name not in PATCHWORK_OWNED_NOISE:
            return False
        if child.name != ".venv":
            return True
        venv_metadata = _coerce_mapping(metadata.get("venv"))
        is_link = child.is_symlink() or _is_reparse_point(child)
        if venv_metadata:
            return bool(venv_metadata.get("linked")) and is_link
        return is_link

    def sync_worktree(self, number: int, *, worktree: Path) -> dict[str, Any]:
        """Fetch the current PR head and move an existing worktree to it."""

        if not worktree.exists():
            raise PullRequestOverseeError(f"Worktree path does not exist: {worktree}")
        remote_ref = f"refs/remotes/origin/pr/{number}"
        self.git(["fetch", "origin", f"pull/{number}/head:{remote_ref}"])
        result = self.runner.run(
            ["git", "-C", str(worktree), "checkout", "--detach", remote_ref],
            cwd=self.cwd,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip()
            raise PullRequestOverseeError(
                f"Unable to sync PR worktree {worktree}: {message}"
            )
        return {
            "number": number,
            "worktree": str(worktree),
            "remoteRef": remote_ref,
            "returncode": result.returncode,
        }

    def merge(
        self,
        number: int,
        *,
        method: str = "squash",
        delete_branch: bool = False,
        require_approval: bool = False,
        expected_head_sha: str = "",
        allow_pending: bool = False,
        admin: bool = False,
    ) -> dict[str, Any]:
        gate = self.gate(
            number, require_approval=require_approval, allow_pending=allow_pending
        )
        if not gate["ready"]:
            raise PullRequestOverseeError(
                "PR is not merge-ready: " + ", ".join(gate["blockers"])
            )
        head_sha = str(gate.get("headRefOid") or "")
        if expected_head_sha and expected_head_sha != head_sha:
            raise PullRequestOverseeError(
                f"PR head changed before merge: expected {expected_head_sha}, got {head_sha}"
            )
        command = ["pr", "merge", str(number), "--repo", self.repo, f"--{method}"]
        guard_sha = expected_head_sha or head_sha
        if guard_sha:
            command.extend(["--match-head-commit", guard_sha])
        if delete_branch:
            command.append("--delete-branch")
        if admin:
            command.append("--admin")
        output = self.gh_text(command)
        after = self.pr_view(number)
        return {
            "number": number,
            "merged": str(after.get("state") or "").upper() == "MERGED",
            "command": ["gh", *command],
            "stdout": output,
            "pullRequest": after,
        }

    def cleanup(
        self,
        number: int,
        *,
        worktree: Path | None = None,
        delete_local_branch: str = "",
    ) -> dict[str, Any]:
        pr = self.pr_view(number)
        state = str(pr.get("state") or "").upper()
        if state != "MERGED":
            raise PullRequestOverseeError(
                f"PR #{number} is not merged; refusing cleanup"
            )
        actions: list[dict[str, Any]] = []
        if worktree:
            actions.append(self._remove_worktree(worktree))
        base_branch = str(pr.get("baseRefName") or "main")
        self.git(["fetch", "origin", base_branch, "--prune"])
        actions.append(
            {"action": "fetch-base-prune", "branch": base_branch, "returncode": 0}
        )
        if delete_local_branch:
            result = self.runner.run(
                ["git", "branch", "-D", delete_local_branch],
                cwd=self.cwd,
                check=False,
            )
            actions.append(
                {
                    "action": "delete-local-branch",
                    "branch": delete_local_branch,
                    "returncode": result.returncode,
                    "stderr": result.stderr.strip(),
                }
            )
        return {"number": number, "state": state, "actions": actions}

    def patchwork_hygiene(
        self,
        *,
        root: Path | None = None,
        max_age_days: float = 14.0,
        write: bool = False,
        force_stale_open: bool = False,
    ) -> dict[str, Any]:
        """Report and optionally prune monitor-owned patchwork worktrees."""

        if max_age_days < 0:
            raise PullRequestOverseeError("max_age_days must be zero or positive")
        patchwork_root = (root or default_patchwork_dir()).expanduser()
        if not patchwork_root.exists():
            return {
                "root": str(patchwork_root),
                "exists": False,
                "maxAgeDays": max_age_days,
                "write": write,
                "items": [],
                "pruned": [],
            }

        items: list[dict[str, Any]] = []
        pruned: list[dict[str, Any]] = []
        pending_items: list[dict[str, Any]] = []
        state_numbers: list[int] = []
        now = time.time()
        for metadata_path in sorted(patchwork_root.glob(f"*/{PATCHWORK_METADATA}")):
            worktree = metadata_path.parent
            try:
                metadata = _coerce_mapping(json.loads(metadata_path.read_text()))
            except (OSError, json.JSONDecodeError):
                metadata = {}
            repo = str(metadata.get("repo") or "")
            raw_number = metadata.get("number") or 0
            invalid_number = False
            try:
                number = int(raw_number)
            except (TypeError, ValueError):
                number = 0
                invalid_number = True
            age_days = max(0.0, (now - metadata_path.stat().st_mtime) / 86400)
            reason = ""
            if repo and repo != self.repo:
                reason = "foreign-repo"
            elif invalid_number:
                reason = "invalid-pr-number"
            elif not number:
                reason = "missing-pr-number"
            elif number:
                state_numbers.append(number)
            pending_items.append(
                {
                    "worktree": worktree,
                    "repo": repo,
                    "number": number,
                    "ageDays": age_days,
                    "reason": reason,
                }
            )

        state_lookup = self.pull_request_state_lookup(state_numbers)
        for pending in pending_items:
            worktree = pending["worktree"]
            repo = str(pending["repo"])
            number = int(pending["number"] or 0)
            age_days = float(pending["ageDays"])
            reason = str(pending["reason"])
            state = ""
            if number and not reason:
                state = state_lookup.get(number, "")
                if not state:
                    try:
                        state = str(self.pr_view(number).get("state") or "").upper()
                    except PullRequestOverseeError as exc:
                        reason = f"pr-state-error:{exc}"

            stale = age_days >= max_age_days
            candidate = state in {"MERGED", "CLOSED"} or (
                force_stale_open and stale and not reason
            )
            if not reason and not candidate:
                reason = "active-or-recent"
            item = {
                "worktree": str(worktree),
                "repo": repo,
                "number": number,
                "state": state,
                "ageDays": round(age_days, 2),
                "stale": stale,
                "candidate": candidate,
                "reason": "prune" if candidate else reason,
            }
            if write and candidate:
                item["remove"] = self._remove_worktree(
                    worktree, patchwork_root=patchwork_root
                )
                pruned.append(item)
            items.append(item)
        return {
            "root": str(patchwork_root),
            "exists": True,
            "maxAgeDays": max_age_days,
            "write": write,
            "items": items,
            "pruned": pruned,
        }

    def hygiene(self, number: int) -> dict[str, Any]:
        return hygiene_report(self.pr_view(number), self.changed_files(number))

    def monitor(
        self,
        number: int,
        *,
        interval_seconds: float = 30.0,
        max_iterations: int = 120,
        timeout_seconds: float = 0.0,
        require_approval: bool = False,
        allow_pending: bool = False,
        include_logs: bool = False,
        run_test_plan: bool = False,
        dependency_limit: int = 80,
        worktree: Path | None = None,
        branch: str = "",
        merge: bool = False,
        cleanup: bool = False,
        method: str = "squash",
        delete_branch: bool = False,
        delete_local_branch: str = "",
        expected_head_sha: str = "",
        admin: bool = False,
        write: bool = False,
    ) -> dict[str, Any]:
        """Run the PR oversight workflow until completion or manual decision."""

        if max_iterations < 0:
            raise PullRequestOverseeError("max_iterations must be zero or positive")
        if interval_seconds < 0:
            raise PullRequestOverseeError("interval_seconds must be zero or positive")
        if timeout_seconds < 0:
            raise PullRequestOverseeError("timeout_seconds must be zero or positive")
        actions: list[dict[str, Any]] = []
        checkout_handled = False
        deadline = self._clock() + timeout_seconds if timeout_seconds else 0.0
        iterations: list[dict[str, Any]] = []
        validation_by_head: dict[str, dict[str, Any]] = {}
        changed_files_by_head: dict[str, list[str]] = {}
        synced_worktree_head = ""
        dependency_dedupe = (
            self.dependency_dedupe(limit=dependency_limit) if dependency_limit else {}
        )
        last_snapshot: dict[str, Any] = {}
        iteration = 0

        while True:
            iteration += 1
            snapshot = self._monitor_snapshot(
                number,
                require_approval=require_approval,
                allow_pending=allow_pending,
                include_logs=include_logs,
                changed_files_by_head=changed_files_by_head,
                dependency_dedupe=dependency_dedupe,
            )
            gate = snapshot["gate"]
            pr = snapshot["inspect"]["pullRequest"]
            head_sha = str(gate.get("headRefOid") or "")

            state = str(pr.get("state") or "").upper()
            validation_would_run = run_test_plan and state != "MERGED"
            if validation_would_run and not write:
                raise PullRequestOverseeError(
                    "monitor --run-test-plan executes local code and requires --write"
                )
            if state != "MERGED" and worktree and not checkout_handled:
                if worktree.exists():
                    actions.append(
                        {"action": "checkout-reuse", "worktree": str(worktree)}
                    )
                else:
                    actions.append(
                        {
                            "action": "checkout",
                            "result": self.checkout(
                                number, worktree=worktree, branch=branch
                            ),
                        }
                    )
                checkout_handled = True
            if (
                state != "MERGED"
                and worktree
                and head_sha
                and synced_worktree_head != head_sha
            ):
                actions.append(
                    {
                        "action": "sync-worktree",
                        "headRefOid": head_sha,
                        "result": self.sync_worktree(number, worktree=worktree),
                    }
                )
                synced_worktree_head = head_sha

            if validation_would_run:
                validation_cwd = worktree if worktree else self.cwd
                validation_head = head_sha or f"iteration-{iteration}"
                validation_key = f"{validation_head}:{validation_cwd}"
                validation = validation_by_head.get(validation_key)
                if validation is None:
                    validation = self.run_validation_commands(
                        snapshot["testPlan"]["commands"],
                        cwd=validation_cwd,
                    )
                    validation_by_head[validation_key] = validation
                    actions.append(
                        {
                            "action": "local-validation",
                            "headRefOid": head_sha,
                            "cwd": str(validation_cwd),
                            "ok": validation["ok"],
                        }
                    )
                snapshot["localValidation"] = validation

            last_snapshot = snapshot
            iteration_summary = {
                "iteration": iteration,
                "state": pr.get("state"),
                "ready": gate.get("ready"),
                "blockers": gate.get("blockers") or [],
                "warnings": gate.get("warnings") or [],
                "hygieneOk": snapshot["hygiene"].get("ok"),
                "ciFailures": len(snapshot["ci"].get("failures") or []),
            }
            iterations.append(iteration_summary)

            if state == "MERGED":
                if cleanup:
                    if not write:
                        return self._monitor_result(
                            number,
                            "manual_decision_required",
                            complete=False,
                            manual_reasons=["write_required:cleanup"],
                            iterations=iterations,
                            last=last_snapshot,
                            actions=actions,
                        )
                    actions.append(
                        {
                            "action": "cleanup",
                            "result": self.cleanup(
                                number,
                                worktree=worktree,
                                delete_local_branch=delete_local_branch,
                            ),
                        }
                    )
                return self._monitor_result(
                    number,
                    "complete",
                    complete=True,
                    manual_reasons=[],
                    iterations=iterations,
                    last=last_snapshot,
                    actions=actions,
                )

            manual_reasons = self._monitor_manual_reasons(snapshot)
            if manual_reasons:
                return self._monitor_result(
                    number,
                    "manual_decision_required",
                    complete=False,
                    manual_reasons=manual_reasons,
                    iterations=iterations,
                    last=last_snapshot,
                    actions=actions,
                )

            if gate.get("ready") and snapshot["hygiene"].get("ok"):
                if not merge:
                    return self._monitor_result(
                        number,
                        "manual_decision_required",
                        complete=False,
                        manual_reasons=["merge_decision_required"],
                        iterations=iterations,
                        last=last_snapshot,
                        actions=actions,
                    )
                if not write:
                    return self._monitor_result(
                        number,
                        "manual_decision_required",
                        complete=False,
                        manual_reasons=["write_required:merge"],
                        iterations=iterations,
                        last=last_snapshot,
                        actions=actions,
                    )
                merge_result = self.merge(
                    number,
                    method=method,
                    delete_branch=delete_branch,
                    require_approval=require_approval,
                    expected_head_sha=expected_head_sha or head_sha,
                    allow_pending=allow_pending,
                    admin=admin,
                )
                actions.append({"action": "merge", "result": merge_result})
                if not merge_result.get("merged"):
                    return self._monitor_result(
                        number,
                        "manual_decision_required",
                        complete=False,
                        manual_reasons=["merge:not_confirmed"],
                        iterations=iterations,
                        last=last_snapshot,
                        actions=actions,
                    )
                if cleanup:
                    actions.append(
                        {
                            "action": "cleanup",
                            "result": self.cleanup(
                                number,
                                worktree=worktree,
                                delete_local_branch=delete_local_branch,
                            ),
                        }
                    )
                return self._monitor_result(
                    number,
                    "complete",
                    complete=True,
                    manual_reasons=[],
                    iterations=iterations,
                    last=last_snapshot,
                    actions=actions,
                )

            if max_iterations and iteration >= max_iterations:
                return self._monitor_result(
                    number,
                    "manual_decision_required",
                    complete=False,
                    manual_reasons=["monitor:max_iterations"],
                    iterations=iterations,
                    last=last_snapshot,
                    actions=actions,
                )
            if deadline and self._clock() >= deadline:
                return self._monitor_result(
                    number,
                    "manual_decision_required",
                    complete=False,
                    manual_reasons=["monitor:timeout"],
                    iterations=iterations,
                    last=last_snapshot,
                    actions=actions,
                )
            self._sleep(interval_seconds)

    def _monitor_snapshot(
        self,
        number: int,
        *,
        require_approval: bool,
        allow_pending: bool,
        include_logs: bool,
        changed_files_by_head: dict[str, list[str]],
        dependency_dedupe: dict[str, Any],
    ) -> dict[str, Any]:
        inspection = self.inspect(
            number,
            require_approval=require_approval,
            allow_pending=allow_pending,
        )
        pr = inspection["pullRequest"]
        gate = inspection["readiness"]
        head_sha = str(gate.get("headRefOid") or pr.get("headRefOid") or "")
        files = changed_files_by_head.get(head_sha)
        if files is None:
            files = self.changed_files(number)
            changed_files_by_head[head_sha] = files
        return {
            "inspect": inspection,
            "gate": gate,
            "hygiene": hygiene_report(pr, files),
            "testPlan": changed_files_to_test_plan(files),
            "ci": self._ci_failures_from_pr(number, pr, include_logs=include_logs),
            "dependencyDedupe": dependency_dedupe,
        }

    def _monitor_manual_reasons(self, snapshot: Mapping[str, Any]) -> list[str]:
        gate = _coerce_mapping(snapshot.get("gate"))
        hygiene = _coerce_mapping(snapshot.get("hygiene"))
        validation = _coerce_mapping(snapshot.get("localValidation"))
        pending_checks = bool(
            _coerce_list(_coerce_mapping(gate.get("checks")).get("pending"))
        )
        reasons = [
            f"gate:{blocker}"
            for blocker in _coerce_list(gate.get("blockers"))
            if not str(blocker).startswith("pending:")
            and not (pending_checks and str(blocker) == "merge_state:BLOCKED")
        ]
        reasons.extend(
            f"hygiene:{failure}" for failure in _coerce_list(hygiene.get("failures"))
        )
        if validation and not validation.get("ok"):
            reasons.append("local_validation:failed")
        return reasons

    def _monitor_result(
        self,
        number: int,
        status: str,
        *,
        complete: bool,
        manual_reasons: list[str],
        iterations: list[dict[str, Any]],
        last: dict[str, Any],
        actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "number": number,
            "repo": self.repo,
            "status": status,
            "complete": complete,
            "manualDecisionRequired": bool(manual_reasons),
            "manualDecisionReasons": manual_reasons,
            "iterationCount": len(iterations),
            "iterations": iterations,
            "last": last,
            "actions": actions,
        }

    def compact_monitor_result(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Return a concise monitor summary for routine waits and merge loops."""

        last = _coerce_mapping(payload.get("last"))
        gate = _coerce_mapping(last.get("gate"))
        inspect_payload = _coerce_mapping(last.get("inspect"))
        pr = _coerce_mapping(inspect_payload.get("pullRequest"))
        readiness = gate or pr
        checks = _coerce_mapping(readiness.get("checks"))
        pending = [
            _check_name(_coerce_mapping(item))
            for item in _coerce_list(checks.get("pending"))
        ]
        failing = [
            _check_name(_coerce_mapping(item))
            for item in _coerce_list(checks.get("failing"))
        ]
        actions = _coerce_list(payload.get("actions"))
        merge_action = next(
            (
                _coerce_mapping(action)
                for action in actions
                if _coerce_mapping(action).get("action") == "merge"
            ),
            {},
        )
        merged = bool(_coerce_mapping(merge_action.get("result")).get("merged"))
        blockers = [str(item) for item in _coerce_list(readiness.get("blockers"))]
        state = str(readiness.get("state") or pr.get("state") or "")
        ready = bool(readiness.get("ready"))
        manual_reasons = [
            str(item) for item in _coerce_list(payload.get("manualDecisionReasons"))
        ]
        manual_decision_required = bool(payload.get("manualDecisionRequired"))
        max_iteration_wait = (
            pending
            and not failing
            and manual_decision_required
            and manual_reasons == ["monitor:max_iterations"]
        )
        if state == "MERGED" or merged:
            outcome = "merged"
            blockers = []
        elif (
            pending
            and not failing
            and (not manual_decision_required or max_iteration_wait)
        ):
            outcome = "waiting"
            if max_iteration_wait:
                manual_decision_required = False
                manual_reasons = []
        elif manual_decision_required:
            outcome = "manual"
        elif ready and not blockers:
            outcome = "ready"
        elif blockers:
            outcome = "blocked"
        else:
            outcome = "complete" if payload.get("complete") else "inspect"

        return {
            "repo": payload.get("repo"),
            "number": payload.get("number") or readiness.get("number"),
            "status": payload.get("status"),
            "outcome": outcome,
            "complete": bool(payload.get("complete")),
            "manualDecisionRequired": manual_decision_required,
            "manualDecisionReasons": manual_reasons,
            "iterationCount": payload.get("iterationCount"),
            "state": state,
            "ready": ready,
            "mergeStateStatus": readiness.get("mergeStateStatus"),
            "mergeable": readiness.get("mergeable"),
            "headRefName": readiness.get("headRefName"),
            "headRefOid": readiness.get("headRefOid"),
            "blockers": blockers,
            "pendingChecks": [name for name in pending if name],
            "failingChecks": [name for name in failing if name],
            "checkCounts": {
                "advisory": len(_coerce_list(checks.get("advisory"))),
                "passing": len(_coerce_list(checks.get("passing"))),
                "pending": len(_coerce_list(checks.get("pending"))),
                "failing": len(_coerce_list(checks.get("failing"))),
                "superseded": len(_coerce_list(checks.get("superseded"))),
            },
            "actions": actions,
        }

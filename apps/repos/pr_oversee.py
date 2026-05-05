"""Deterministic pull-request oversight helpers."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

JSONValue = dict[str, Any] | list[Any] | str | int | float | bool | None

PR_FIELDS = ",".join(
    [
        "number",
        "title",
        "author",
        "body",
        "baseRefName",
        "baseRefOid",
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

GOOD_CHECKS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
BAD_CHECKS = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}
PENDING_CHECKS = {
    "EXPECTED",
    "PENDING",
    "QUEUED",
    "REQUESTED",
    "IN_PROGRESS",
    "WAITING",
}
BAD_MERGE_STATES = {"BEHIND", "BLOCKED", "DIRTY", "UNKNOWN"}
README_RE = re.compile(r"(^|/)(README|README\.[^/]+)$", re.IGNORECASE)
VERSION_SUFFIX_SEPARATORS = "-_/"


class PullRequestOverseeError(RuntimeError):
    """Raised when PR oversight cannot complete deterministically."""


@dataclass(slots=True)
class CommandResult:
    """Subprocess result captured by the command runner."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


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


def _json_loads(raw_value: str) -> JSONValue:
    if not raw_value.strip():
        return None
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise PullRequestOverseeError(
            f"Command did not return valid JSON: {exc}"
        ) from exc


def _coerce_mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _author_login(pr: Mapping[str, Any]) -> str:
    author = pr.get("author")
    if isinstance(author, Mapping):
        return str(author.get("login") or "")
    return str(author or "")


def _check_name(check: Mapping[str, Any]) -> str:
    return str(
        check.get("name")
        or check.get("context")
        or check.get("workflowName")
        or check.get("workflow")
        or "check"
    )


def check_rollup_state(pr: Mapping[str, Any]) -> dict[str, list[dict[str, str]]]:
    """Classify status check rollup entries as failing, pending, or passing."""

    failing: list[dict[str, str]] = []
    pending: list[dict[str, str]] = []
    passing: list[dict[str, str]] = []
    for raw_check in _coerce_list(pr.get("statusCheckRollup")):
        check = _coerce_mapping(raw_check)
        name = _check_name(check)
        conclusion = str(check.get("conclusion") or "").upper()
        status = str(check.get("status") or "").upper()
        state = str(check.get("state") or "").upper()
        value = conclusion or state or status or "UNKNOWN"
        entry = {
            "name": name,
            "status": status,
            "state": state,
            "conclusion": conclusion,
            "value": value,
            "detailsUrl": str(check.get("detailsUrl") or check.get("link") or ""),
        }
        if conclusion in BAD_CHECKS or state in BAD_CHECKS:
            failing.append(entry)
        elif conclusion and conclusion not in GOOD_CHECKS:
            failing.append(entry)
        elif state and state not in GOOD_CHECKS and state not in {"COMPLETED"}:
            if state in PENDING_CHECKS:
                pending.append(entry)
            else:
                failing.append(entry)
        elif status and status not in {"COMPLETED", "SUCCESS"}:
            if status in PENDING_CHECKS:
                pending.append(entry)
            else:
                failing.append(entry)
        else:
            passing.append(entry)
    return {"failing": failing, "pending": pending, "passing": passing}


def readiness_gate(
    pr: Mapping[str, Any],
    *,
    require_approval: bool = False,
    allow_pending: bool = False,
    require_conversation_resolution: bool = True,
) -> dict[str, Any]:
    """Return deterministic PR readiness blockers and warnings."""

    blockers: list[str] = []
    warnings: list[str] = []
    if pr.get("isDraft"):
        blockers.append("draft")

    merge_state = str(pr.get("mergeStateStatus") or "").upper()
    if merge_state in BAD_MERGE_STATES:
        blockers.append(f"merge_state:{merge_state}")
    elif not merge_state:
        warnings.append("merge_state:EMPTY")

    mergeable = str(pr.get("mergeable") or "").upper()
    if mergeable in {"CONFLICTING", "FALSE"}:
        blockers.append(f"mergeable:{mergeable}")

    review_decision = str(pr.get("reviewDecision") or "").upper()
    if review_decision in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
        blockers.append(f"review:{review_decision}")
    elif require_approval and review_decision != "APPROVED":
        blockers.append(f"review:{review_decision or 'MISSING_APPROVAL'}")

    checks = check_rollup_state(pr)
    blockers.extend(
        f"check:{item['name']}:{item['value']}" for item in checks["failing"]
    )
    pending_values = [
        f"pending:{item['name']}:{item['value']}" for item in checks["pending"]
    ]
    if pending_values and allow_pending:
        warnings.extend(pending_values)
    else:
        blockers.extend(pending_values)

    unresolved_threads = int(pr.get("unresolvedReviewThreadCount") or 0)
    if require_conversation_resolution and unresolved_threads:
        blockers.append(f"review_threads:UNRESOLVED:{unresolved_threads}")

    return {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "author": _author_login(pr),
        "url": pr.get("url"),
        "headRefName": pr.get("headRefName"),
        "headRefOid": pr.get("headRefOid"),
        "baseRefName": pr.get("baseRefName"),
        "baseRefOid": pr.get("baseRefOid"),
        "ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "mergeStateStatus": pr.get("mergeStateStatus"),
        "mergeable": pr.get("mergeable"),
        "reviewDecision": pr.get("reviewDecision"),
        "checks": checks,
        "unresolvedReviewThreadCount": unresolved_threads,
        "updatedAt": pr.get("updatedAt"),
    }


def _split_marker(value: str, marker: str) -> tuple[str, str] | None:
    index = value.lower().find(marker)
    if index == -1:
        return None
    return value[:index], value[index + len(marker) :]


def _normalize_dependency_name(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _parse_dependency_title(title: str) -> tuple[str, str] | None:
    stripped = title.strip()
    lowered = stripped.lower()
    if lowered.startswith("bump "):
        body = stripped[5:].strip()
    elif lowered.startswith("update dependency "):
        body = stripped[len("update dependency ") :].strip()
    else:
        return None

    from_split = _split_marker(body, " from ")
    if from_split:
        name, remaining = from_split
        to_split = _split_marker(remaining, " to ")
        if to_split:
            return name.strip(), to_split[1].strip().split(maxsplit=1)[0]
        return None

    to_split = _split_marker(body, " to ")
    if to_split:
        return to_split[0].strip(), to_split[1].strip().split(maxsplit=1)[0]
    return None


def _looks_like_version_suffix(value: str) -> bool:
    normalized = value.strip().lstrip("vV")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    return (
        bool(normalized)
        and normalized[0].isdigit()
        and "." in normalized
        and all(character in allowed for character in normalized)
    )


def _version_suffix_match(value: str) -> tuple[int, str] | None:
    for index, character in enumerate(value):
        if character not in VERSION_SUFFIX_SEPARATORS:
            continue
        candidate = value[index + 1 :]
        if _looks_like_version_suffix(candidate):
            return index, candidate.lstrip("vV")
    return None


def _version_suffix(value: str) -> str:
    match = _version_suffix_match(value)
    return match[1] if match else ""


def _strip_version_suffix(value: str) -> str:
    match = _version_suffix_match(value)
    if not match:
        return value
    return value[: match[0]].rstrip(VERSION_SUFFIX_SEPARATORS)


def dependency_key(pr: Mapping[str, Any]) -> str:
    """Return a stable dependency grouping key for a PR."""

    title = str(pr.get("title") or "")
    title_parts = _parse_dependency_title(title)
    if title_parts:
        return _normalize_dependency_name(title_parts[0])
    head = str(pr.get("headRefName") or "").lower()
    if head.startswith("dependabot/"):
        parts = head.split("/", 2)
        head = parts[2] if len(parts) == 3 else parts[-1]
    head = _strip_version_suffix(head)
    return head or title.lower()


def dependency_target_version(pr: Mapping[str, Any]) -> str:
    """Best-effort dependency target version from title or branch."""

    title = str(pr.get("title") or "")
    title_parts = _parse_dependency_title(title)
    if title_parts:
        return title_parts[1]
    head = str(pr.get("headRefName") or "")
    return _version_suffix(head)


def is_dependency_pr(pr: Mapping[str, Any]) -> bool:
    """Return True when a PR appears to be a dependency update."""

    login = _author_login(pr).lower()
    title = str(pr.get("title") or "").lower()
    head = str(pr.get("headRefName") or "").lower()
    return (
        "dependabot" in login
        or "dependabot" in head
        or title.startswith("build(deps")
        or "bump " in title
    )


def dependency_duplicates(prs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Group duplicate or superseded dependency PR candidates."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for pr in prs:
        if not is_dependency_pr(pr):
            continue
        key = dependency_key(pr)
        grouped.setdefault(key, []).append(
            {
                "number": pr.get("number"),
                "title": pr.get("title"),
                "headRefName": pr.get("headRefName"),
                "targetVersion": dependency_target_version(pr),
                "updatedAt": pr.get("updatedAt"),
                "url": pr.get("url"),
            }
        )

    duplicates: dict[str, Any] = {}
    for key, items in grouped.items():
        if len(items) < 2:
            continue
        ordered = sorted(items, key=lambda item: str(item.get("updatedAt") or ""))
        duplicates[key] = {
            "items": ordered,
            "superseded": ordered[:-1],
            "preferred": ordered[-1],
        }
    return duplicates


def changed_files_to_test_plan(paths: Iterable[str]) -> dict[str, Any]:
    """Map changed file paths to deterministic local validation commands."""

    files = sorted({path for path in paths if path})
    apps: set[str] = set()
    commands: list[list[str]] = []
    notes: list[str] = []
    model_change = False
    migration_change = False
    docs_only = bool(files)
    workflow_change = False
    generated_candidates: list[str] = []

    for path in files:
        if path.startswith("apps/"):
            docs_only = False
            parts = path.split("/")
            if len(parts) > 1:
                apps.add(parts[1])
            if "/models" in path or path.endswith("models.py"):
                model_change = True
            if "/migrations/" in path:
                migration_change = True
        elif not path.startswith("docs/") and not path.endswith(".md"):
            docs_only = False
        if path.startswith(".github/workflows/"):
            workflow_change = True
        if path.endswith((".pyc", ".sqlite3", ".log")) or "__pycache__" in path:
            generated_candidates.append(path)

    commands.append([sys.executable, "manage.py", "check", "--fail-level", "ERROR"])
    test_paths = [f"apps/{app_label}/tests" for app_label in sorted(apps)]
    if test_paths:
        commands.append([sys.executable, "manage.py", "test", "run", "--", *test_paths])
    if model_change or migration_change:
        commands.append(
            [sys.executable, "manage.py", "makemigrations", "--check", "--dry-run"]
        )
        commands.append([sys.executable, "manage.py", "migrate", "--check"])
    if workflow_change:
        notes.append(
            "Workflow files changed; inspect GitHub Actions syntax and required checks."
        )
    if docs_only:
        notes.append(
            "Docs-only change; app tests may be unnecessary beyond Django checks."
        )
    if generated_candidates:
        notes.append("Generated artifacts detected: " + ", ".join(generated_candidates))

    return {
        "files": files,
        "apps": sorted(apps),
        "modelChange": model_change,
        "migrationChange": migration_change,
        "docsOnly": docs_only,
        "commands": commands,
        "notes": notes,
    }


def hygiene_report(pr: Mapping[str, Any], files: Iterable[str]) -> dict[str, Any]:
    """Return deterministic PR hygiene warnings and failures."""

    body = str(pr.get("body") or "")
    changed = sorted({path for path in files if path})
    warnings: list[str] = []
    failures: list[str] = []
    lower_body = body.lower()
    if "summary" not in lower_body:
        warnings.append("body:missing-summary")
    if "validation" not in lower_body and "test" not in lower_body:
        warnings.append("body:missing-validation")
    if not re.search(
        r"\b(close[sd]?|fix(e[sd])?|resolve[sd]?)\s+#\d+", body, re.IGNORECASE
    ):
        warnings.append("body:missing-issue-link")

    model_paths = [
        path
        for path in changed
        if path.startswith("apps/")
        and ("/models" in path or path.endswith("models.py"))
    ]
    migration_paths = [path for path in changed if "/migrations/" in path]
    if model_paths and not migration_paths:
        failures.append("model-change:missing-migration")
    if any(README_RE.search(path) for path in changed):
        warnings.append("readme:changed")
    generated = [
        path
        for path in changed
        if path.endswith((".pyc", ".sqlite3", ".log")) or "__pycache__" in path
    ]
    if generated:
        failures.append("generated-artifacts:" + ",".join(generated))
    if (
        changed
        and not any(path.startswith("docs/") for path in changed)
        and len(changed) > 5
    ):
        warnings.append("docs:not-updated")

    return {
        "ok": not failures,
        "failures": failures,
        "warnings": warnings,
        "files": changed,
    }


class PullRequestOverseer:
    """Command-backed deterministic PR oversight surface."""

    def __init__(
        self,
        *,
        repo: str,
        runner: CommandRunner | None = None,
        cwd: Path | None = None,
    ) -> None:
        self.repo = repo
        self.runner = runner or CommandRunner()
        self.cwd = cwd or Path.cwd()

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
        return _coerce_mapping(payload)

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
                "number,title,author,headRefName,baseRefName,isDraft,mergeStateStatus,reviewDecision,statusCheckRollup,url,updatedAt",
            ]
        )
        return [_coerce_mapping(item) for item in _coerce_list(payload)]

    def comments(self, number: int, *, unresolved_only: bool = False) -> dict[str, Any]:
        owner, name = self.repo.split("/", 1)
        query = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        nodes {
          isResolved
          isOutdated
          path
          line
          comments(first: 50) {
            nodes {
              author { login }
              body
              createdAt
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
                        "author": str(
                            _coerce_mapping(comment.get("author")).get("login") or ""
                        ),
                        "body": str(comment.get("body") or ""),
                        "createdAt": str(comment.get("createdAt") or ""),
                        "url": str(comment.get("url") or ""),
                        "path": str(comment.get("path") or thread.get("path") or ""),
                        "line": comment.get("line") or thread.get("line"),
                    }
                )
            normalized.append(
                {
                    "isResolved": is_resolved,
                    "isOutdated": bool(thread.get("isOutdated")),
                    "path": str(thread.get("path") or ""),
                    "line": thread.get("line"),
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

    def inspect(self, number: int) -> dict[str, Any]:
        pr = self.pr_view(number)
        review_threads = self.comments(number, unresolved_only=False)
        pr["reviewThreads"] = review_threads["threads"]
        pr["unresolvedReviewThreadCount"] = review_threads["unresolvedCount"]
        return {
            "pullRequest": pr,
            "readiness": readiness_gate(pr),
        }

    def gate(
        self,
        number: int,
        *,
        require_approval: bool = False,
        allow_pending: bool = False,
    ) -> dict[str, Any]:
        pr = self.inspect(number)["pullRequest"]
        return readiness_gate(
            pr, require_approval=require_approval, allow_pending=allow_pending
        )

    def changed_files(self, number: int) -> list[str]:
        output = self.gh_text(
            ["pr", "diff", str(number), "--repo", self.repo, "--name-only"]
        )
        return [line.strip() for line in output.splitlines() if line.strip()]

    def test_plan(self, number: int) -> dict[str, Any]:
        return changed_files_to_test_plan(self.changed_files(number))

    def ci_failures(
        self, number: int, *, include_logs: bool = False, log_limit: int = 4000
    ) -> dict[str, Any]:
        pr = self.pr_view(number)
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

    def dependency_dedupe(self, *, limit: int = 80) -> dict[str, Any]:
        return dependency_duplicates(self.list_open_prs(limit=limit))

    def checkout(
        self,
        number: int,
        *,
        worktree: Path,
        branch: str = "",
    ) -> dict[str, Any]:
        if worktree.exists():
            raise PullRequestOverseeError(f"Worktree path already exists: {worktree}")
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
        try:
            (worktree / ".arthexis-pr-oversee.json").write_text(
                json.dumps(metadata, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            metadata["metadataWriteError"] = True
        return metadata

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
            result = self.runner.run(
                ["git", "worktree", "remove", str(worktree)], cwd=self.cwd, check=False
            )
            actions.append(
                {
                    "action": "remove-worktree",
                    "path": str(worktree),
                    "returncode": result.returncode,
                    "stderr": result.stderr.strip(),
                }
            )
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

    def hygiene(self, number: int) -> dict[str, Any]:
        return hygiene_report(self.pr_view(number), self.changed_files(number))

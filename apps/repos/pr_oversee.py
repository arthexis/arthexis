"""Deterministic pull-request oversight helpers."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
PATCHWORK_ENV_VAR = "ARTHEXIS_PATCHWORK_DIR"
PATCHWORK_METADATA = ".arthexis-pr-oversee.json"
PATCHWORK_OWNED_NOISE = {PATCHWORK_METADATA, ".venv"}


def default_patchwork_dir() -> Path:
    """Return the default directory for temporary PR worktrees."""

    configured = os.environ.get(PATCHWORK_ENV_VAR, "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "patchwork"


def _slugify_path_segment(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return slug.strip("-") or "repo"


def patchwork_worktree_path(root: Path, repo: str, number: int) -> Path:
    """Return the deterministic patchwork worktree path for a PR."""

    return root.expanduser() / f"{_slugify_path_segment(repo)}-pr-{number}"


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _status_path(line: str) -> str:
    if len(line) < 4:
        return ""
    value = line[3:].strip()
    if " -> " in value:
        return ""
    return value.rstrip("/")


def _status_is_patchwork_noise(lines: Iterable[str]) -> bool:
    paths = [_status_path(line) for line in lines if line.strip()]
    if not paths:
        return True
    return all(
        path in PATCHWORK_OWNED_NOISE
        or any(path.startswith(f"{noise}/") for noise in PATCHWORK_OWNED_NOISE)
        for path in paths
    )


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _remove_owned_path(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
        return
    if _is_reparse_point(path):
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
        return
    if path.is_dir():
        shutil.rmtree(path)
        return
    path.unlink(missing_ok=True)


def _local_venv_link(source: Path, target: Path) -> dict[str, Any]:
    if target.exists() or target.is_symlink():
        return {
            "linked": False,
            "reason": "target-exists",
            "source": str(source),
            "target": str(target),
        }
    if not source.exists():
        return {
            "linked": False,
            "reason": "source-missing",
            "source": str(source),
            "target": str(target),
        }

    resolved_source = source.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "nt":
            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(target), str(resolved_source)],
                check=False,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                text=True,
            )
            if completed.returncode != 0:
                return {
                    "linked": False,
                    "reason": "junction-failed",
                    "source": str(resolved_source),
                    "target": str(target),
                    "stderr": completed.stderr.strip(),
                    "stdout": completed.stdout.strip(),
                }
            kind = "junction"
        else:
            target.symlink_to(resolved_source, target_is_directory=True)
            kind = "symlink"
    except OSError as exc:
        return {
            "linked": False,
            "reason": "link-failed",
            "source": str(resolved_source),
            "target": str(target),
            "error": str(exc),
        }
    return {
        "linked": True,
        "kind": kind,
        "source": str(resolved_source),
        "target": str(target),
    }


def _git_worktree_missing_error(*results: Any) -> bool:
    message = " ".join(f"{result.stdout} {result.stderr}".lower() for result in results)
    return any(
        marker in message
        for marker in (
            "not a working tree",
            "not a git repository",
            "is not a working tree",
            "does not exist",
        )
    )


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


def _check_group_key(check: Mapping[str, Any]) -> tuple[str, str, str]:
    app = _coerce_mapping(check.get("app"))
    return (
        _check_name(check),
        str(check.get("workflowName") or check.get("workflow") or ""),
        str(app.get("name") or ""),
    )


def _check_order_key(check: Mapping[str, Any], index: int) -> tuple[int, str]:
    for key in ("completedAt", "startedAt", "updatedAt", "createdAt"):
        value = str(check.get(key) or "")
        if value and not value.startswith("0001-"):
            return 1, value
    return 0, f"{index:08d}"


def _check_entry(check: Mapping[str, Any]) -> dict[str, str]:
    name = _check_name(check)
    conclusion = str(check.get("conclusion") or "").upper()
    status = str(check.get("status") or "").upper()
    state = str(check.get("state") or "").upper()
    return {
        "name": name,
        "status": status,
        "state": state,
        "conclusion": conclusion,
        "value": conclusion or state or status or "UNKNOWN",
        "detailsUrl": str(
            check.get("detailsUrl") or check.get("targetUrl") or check.get("link") or ""
        ),
    }


def check_rollup_state(pr: Mapping[str, Any]) -> dict[str, list[dict[str, str]]]:
    """Classify status check rollup entries as failing, pending, or passing."""

    latest: dict[
        tuple[str, str, str], tuple[tuple[int, str], int, Mapping[str, Any]]
    ] = {}
    superseded: list[dict[str, str]] = []
    for index, raw_check in enumerate(_coerce_list(pr.get("statusCheckRollup"))):
        check = _coerce_mapping(raw_check)
        key = _check_group_key(check)
        order = _check_order_key(check, index)
        previous = latest.get(key)
        if previous is None or order >= previous[0]:
            if previous is not None:
                superseded.append(_check_entry(previous[2]))
            latest[key] = (order, index, check)
        else:
            superseded.append(_check_entry(check))

    failing: list[dict[str, str]] = []
    pending: list[dict[str, str]] = []
    passing: list[dict[str, str]] = []
    for _order, _index, check in sorted(latest.values(), key=lambda item: item[1]):
        entry = _check_entry(check)
        conclusion = entry["conclusion"]
        status = entry["status"]
        state = entry["state"]
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
    return {
        "failing": failing,
        "pending": pending,
        "passing": passing,
        "superseded": superseded,
    }


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


def review_reply_summary(
    *,
    commit: str = "",
    changes: Iterable[str] = (),
    validations: Iterable[str] = (),
    notes: Iterable[str] = (),
) -> dict[str, Any]:
    """Build a terse review-thread reply body from structured inputs."""

    cleaned_changes = [item.strip() for item in changes if item.strip()]
    cleaned_validations = [item.strip() for item in validations if item.strip()]
    cleaned_notes = [item.strip() for item in notes if item.strip()]
    short_commit = commit.strip()[:12]
    lines = [f"Addressed in {short_commit}." if short_commit else "Addressed."]
    if cleaned_changes:
        lines.extend(["", "Changes:"])
        lines.extend(f"- {item}" for item in cleaned_changes)
    if cleaned_validations:
        lines.extend(["", "Validation:"])
        lines.extend(f"- {item}" for item in cleaned_validations)
    if cleaned_notes:
        lines.extend(["", "Notes:"])
        lines.extend(f"- {item}" for item in cleaned_notes)
    return {
        "commit": short_commit,
        "changes": cleaned_changes,
        "validations": cleaned_validations,
        "notes": cleaned_notes,
        "body": "\n".join(lines).strip() + "\n",
    }


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

    def test_plan(self, number: int) -> dict[str, Any]:
        return changed_files_to_test_plan(self.changed_files(number))

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
                int(item["priority"]) if "priority" in item else 99,
                str(item.get("updatedAt") or ""),
                int(item.get("number") or 0),
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
            "actions": self.execute_actions(action_plans) if write else [],
        }

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
        item = self._advance_item(pr, gate, hygiene)
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
    ) -> dict[str, Any]:
        number = int(pr.get("number") or 0)
        blockers = [str(item) for item in _coerce_list(gate.get("blockers"))]
        non_draft_blockers = [item for item in blockers if item != "draft"]
        is_draft = bool(pr.get("isDraft"))
        hygiene_ok = bool(hygiene.get("ok"))
        ready_to_merge = bool(gate.get("ready")) and hygiene_ok and not is_draft
        can_mark_ready = is_draft and not non_draft_blockers and hygiene_ok
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
                "--untracked-files=all",
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
        result = self.runner.run(
            ["git", "worktree", "remove", str(worktree)], cwd=self.cwd, check=False
        )
        action: dict[str, Any] = {
            "action": "remove-worktree",
            "path": str(worktree),
            "returncode": result.returncode,
            "stderr": result.stderr.strip(),
        }
        if result.returncode == 0:
            residue = self._remove_patchwork_residue(
                worktree, patchwork_root=patchwork_root
            )
            if residue.get("attempted"):
                action["residue"] = residue
            return action

        metadata_exists = (worktree / PATCHWORK_METADATA).exists()
        status_lines = self._worktree_status_lines(worktree)
        can_force = metadata_exists and _status_is_patchwork_noise(status_lines)
        if patchwork_root is not None:
            can_force = can_force and _path_is_relative_to(worktree, patchwork_root)
        if not can_force:
            action["forced"] = False
            action["status"] = status_lines
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

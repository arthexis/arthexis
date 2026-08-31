"""Git subprocess adapter and helper operations for release publishing.

Responsibilities:
- Provide a narrow command wrapper around git subprocess calls.
- Normalize subprocess/authentication errors for caller-facing messaging.

Allowed dependencies:
- Stdlib subprocess/path utilities and shared constants.
- Must not import Django HTTP view code.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Protocol, Sequence

from apps.core.views.reports.common import DIRTY_STATUS_LABELS


class GitProcessAdapter(Protocol):
    """Minimal git command adapter to allow deterministic tests."""

    def run(
        self,
        args: Sequence[str],
        *,
        check: bool = True,
        input_text: str | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess: ...


@dataclass
class SubprocessGitAdapter:
    """Production adapter executing git commands through ``subprocess.run``."""

    def run(
        self,
        args: Sequence[str],
        *,
        check: bool = True,
        input_text: str | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess:
        cmd_timeout = timeout
        if cmd_timeout is None:
            try:
                cmd_timeout = float(os.environ.get("GIT_CMD_TIMEOUT", "120"))
            except ValueError:
                cmd_timeout = 120.0
        return subprocess.run(
            args,
            check=check,
            capture_output=True,
            input=input_text,
            text=True,
            timeout=cmd_timeout,
        )


def format_subprocess_error(exc: subprocess.CalledProcessError) -> str:
    """Return best-effort stderr/stdout detail for a failed git command."""

    return (getattr(exc, "stderr", "") or getattr(exc, "stdout", "") or str(exc)).strip()


def git_authentication_missing(exc: subprocess.CalledProcessError) -> bool:
    """Identify common git authentication failure signatures."""

    message = format_subprocess_error(exc).lower()
    markers = (
        "could not read username",
        "authentication failed",
        "terminal prompts disabled",
        "permission denied (publickey)",
    )
    return any(marker in message for marker in markers)


def git_stdout(adapter: GitProcessAdapter, args: Sequence[str]) -> str:
    """Run a git command and return stripped stdout."""

    proc = adapter.run(args, check=True)
    return (proc.stdout or "").strip()


def working_tree_dirty(adapter: GitProcessAdapter) -> bool:
    """Return ``True`` when ``git status --porcelain`` has output."""

    try:
        proc = adapter.run(["git", "status", "--porcelain"], check=False)
    except subprocess.TimeoutExpired:
        raise
    except subprocess.SubprocessError:
        return False
    if proc.returncode != 0:
        return False
    return bool((proc.stdout or "").strip())


def current_branch(adapter: GitProcessAdapter) -> str | None:
    """Return current branch name or ``None`` when detached."""

    branch = git_stdout(adapter, ["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return None if branch == "HEAD" else branch


def has_upstream(adapter: GitProcessAdapter, branch: str) -> bool:
    """Return whether the branch has an upstream tracking reference."""

    proc = adapter.run(["git", "rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"], check=False)
    return proc.returncode == 0


def collect_dirty_files(adapter: GitProcessAdapter) -> list[dict[str, str]]:
    """Collect changed file metadata from porcelain output."""

    proc = adapter.run(["git", "status", "--porcelain"], check=True)
    dirty: list[dict[str, str]] = []
    for line in (proc.stdout or "").splitlines():
        if not line.strip():
            continue
        status_code = line[:2]
        status = status_code.strip() or status_code
        path = line[3:]
        if "R" in status and " -> " in path:
            path = path.split(" -> ", 1)[1]
        dirty.append({"path": path, "status": status, "status_label": DIRTY_STATUS_LABELS.get(status, status)})
    return dirty


def push_needed(adapter: GitProcessAdapter, remote: str, branch: str) -> bool:
    """Return true when local branch head differs from remote branch head."""

    local = git_stdout(adapter, ["git", "rev-parse", branch])
    remote_proc = adapter.run(["git", "ls-remote", "--heads", remote, branch], check=False)
    if remote_proc.returncode != 0:
        return True
    remote_head = ""
    for line in (remote_proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].endswith(f"/{branch}"):
            remote_head = parts[0]
            break
    return not remote_head or remote_head != local

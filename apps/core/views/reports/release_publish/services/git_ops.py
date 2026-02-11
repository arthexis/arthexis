"""Git subprocess adapter and helper operations for release publishing."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Protocol, Sequence

from ...common import DIRTY_STATUS_LABELS


class GitProcessAdapter(Protocol):
    """Minimal git command adapter to allow deterministic tests."""

    def run(self, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess: ...


@dataclass
class SubprocessGitAdapter:
    """Production adapter executing git commands through ``subprocess.run``."""

    def run(self, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(args, check=check, capture_output=True, text=True)


def git_stdout(adapter: GitProcessAdapter, args: Sequence[str]) -> str:
    """Run a git command and return stripped stdout."""

    proc = adapter.run(args, check=True)
    return (proc.stdout or "").strip()


def working_tree_dirty(adapter: GitProcessAdapter) -> bool:
    """Return ``True`` when ``git status --porcelain`` has output."""

    try:
        proc = adapter.run(["git", "status", "--porcelain"], check=True)
    except Exception:
        return False
    return bool((proc.stdout or "").strip())


def has_upstream(adapter: GitProcessAdapter, branch: str) -> bool:
    """Return whether the branch has an upstream tracking reference."""

    proc = adapter.run(
        ["git", "rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"],
        check=False,
    )
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
        dirty.append(
            {
                "path": path,
                "status": status,
                "status_label": DIRTY_STATUS_LABELS.get(status, status),
            }
        )
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

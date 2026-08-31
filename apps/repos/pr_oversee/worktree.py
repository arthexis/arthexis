"""Patchwork worktree pathing and cleanup helpers."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

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
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


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


def _unlink_owned_venv_link(
    worktree: Path,
    *,
    metadata: Mapping[str, Any] | None = None,
    patchwork_root: Path | None = None,
) -> dict[str, Any]:
    if patchwork_root is not None and not _path_is_relative_to(
        worktree, patchwork_root
    ):
        return {"attempted": False, "reason": "outside-patchwork-root"}
    target = worktree / ".venv"
    if not target.exists() and not target.is_symlink():
        return {"attempted": False, "reason": "missing"}
    is_link = target.is_symlink() or _is_reparse_point(target)
    if not is_link:
        return {"attempted": False, "reason": "not-link"}
    venv_metadata = metadata.get("venv") if metadata else None
    if (
        not isinstance(venv_metadata, Mapping)
        or not venv_metadata.get("linked")
        or not venv_metadata.get("source")
    ):
        return {"attempted": False, "reason": "metadata-not-restorable"}
    try:
        _remove_owned_path(target)
    except OSError as exc:
        return {"attempted": True, "removed": False, "error": str(exc)}
    return {"attempted": True, "removed": not target.exists(), "path": ".venv"}


def _restore_owned_venv_link(
    worktree: Path,
    *,
    metadata: Mapping[str, Any] | None = None,
    patchwork_root: Path | None = None,
) -> dict[str, Any]:
    if patchwork_root is not None and not _path_is_relative_to(
        worktree, patchwork_root
    ):
        return {"attempted": False, "reason": "outside-patchwork-root"}
    target = worktree / ".venv"
    if target.exists() or target.is_symlink():
        return {"attempted": False, "reason": "target-exists"}
    venv_metadata = metadata.get("venv") if metadata else None
    if not isinstance(venv_metadata, Mapping) or not venv_metadata.get("linked"):
        return {"attempted": False, "reason": "metadata-not-linked"}
    source_value = str(venv_metadata.get("source") or "").strip()
    if not source_value:
        return {"attempted": False, "reason": "source-missing"}
    restored = _local_venv_link(Path(source_value), target)
    return {"attempted": True, "restored": bool(restored.get("linked")), **restored}


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

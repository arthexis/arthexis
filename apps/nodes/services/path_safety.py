"""Filesystem path guards for node-owned local assets."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from django.core.exceptions import SuspiciousFileOperation
from django.utils._os import safe_join

if TYPE_CHECKING:
    from apps.nodes.models import Node


def _resolve_within(root: Path, candidate: Path) -> Path | None:
    """Return ``candidate`` resolved under ``root`` or ``None`` when it escapes."""

    try:
        resolved_root = root.resolve(strict=False)
        candidate_path = Path(candidate)
        if candidate_path.is_absolute():
            relative_candidate = None
            for base_path in (resolved_root, root):
                try:
                    relative_candidate = candidate_path.relative_to(base_path)
                    break
                except ValueError:
                    continue
            if relative_candidate is None:
                return None
        else:
            try:
                relative_candidate = candidate_path.relative_to(root)
            except ValueError:
                relative_candidate = candidate_path
        safe_candidate = Path(safe_join(resolved_root, str(relative_candidate)))
        resolved_candidate = safe_candidate.resolve(strict=False)
        if not resolved_candidate.is_relative_to(resolved_root):
            return None
    except (OSError, RuntimeError, SuspiciousFileOperation, ValueError):
        return None
    return safe_candidate


def resolve_node_security_file(node: Node, filename: str) -> Path | None:
    """Return a safe path for a node security file name."""

    clean_name = str(filename or "").strip()
    if not clean_name or Path(clean_name).name != clean_name:
        return None
    security_dir = node.get_base_path() / "security"
    return _resolve_within(security_dir, security_dir / clean_name)


def resolve_node_ipc_socket_path(node: Node) -> Path | None:
    """Return a safe sibling IPC socket path for ``node``."""

    socket_path = node.get_ipc_socket_path()
    if socket_path is None:
        return None
    return resolve_ipc_socket_path(node, socket_path)


def resolve_ipc_socket_path(node: Node, socket_path: Path) -> Path | None:
    """Return ``socket_path`` only when it stays inside the node IPC directory."""

    managed_root = node.get_base_path() / "ipc"
    return _resolve_within(managed_root, Path(socket_path))

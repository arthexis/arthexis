from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from django.db import DatabaseError
from django.utils import timezone

from apps.core.auto_upgrade import append_auto_upgrade_log


logger = logging.getLogger(__name__)

CANARY_LIVE_GRACE_MINUTES = 10


def _load_upgrade_canaries() -> list["Node"]:
    try:
        from apps.nodes.models import Node
    except ImportError:  # pragma: no cover - import safety
        return []

    try:
        local = Node.get_local()
    except (DatabaseError, Node.DoesNotExist):  # pragma: no cover - database or config failure
        return []

    if local is None:
        return []

    try:
        return list(local.upgrade_canaries.all())
    except DatabaseError:  # pragma: no cover - database unavailable
        return []


def _canary_is_live(node: "Node", *, now: datetime) -> bool:
    if not getattr(node, "last_updated", None):
        return False
    return node.last_updated >= now - timedelta(minutes=CANARY_LIVE_GRACE_MINUTES)


def _resolve_canary_target(
    repo_state: "AutoUpgradeRepositoryState",
    mode: "AutoUpgradeMode",
) -> tuple[str | None, str | None]:
    if mode.mode == "unstable":
        return "revision", repo_state.remote_revision
    if repo_state.release_revision:
        return "revision", repo_state.release_revision
    target_version = repo_state.remote_version or repo_state.local_version
    return ("version", target_version) if target_version else (None, None)


def _canary_matches_target(
    node: "Node", target_type: str | None, target_value: str | None
) -> bool:
    if not target_type or not target_value:
        return False
    if target_type == "revision":
        return (node.installed_revision or "").strip() == target_value
    return (node.installed_version or "").strip() == target_value


def _format_canary_state(
    node: "Node",
    *,
    live: bool,
    matches_target: bool,
    target_type: str | None,
    target_value: str | None,
) -> str:
    identifier = node.hostname or f"node-{node.pk}"
    parts = ["live" if live else "offline"]
    if target_type and target_value:
        label = "revision" if target_type == "revision" else "version"
        status = "ready" if matches_target else "pending"
        parts.append(f"{label} {status} ({target_value})")
    else:
        parts.append("target unknown")
    return f"{identifier}: {', '.join(parts)}"


def _canary_gate(
    base_dir: Path,
    repo_state: "AutoUpgradeRepositoryState",
    mode: "AutoUpgradeMode",
    *,
    now: datetime | None = None,
) -> bool:
    if not mode.requires_canaries:
        return True
    canaries = _load_upgrade_canaries()
    if not canaries:
        append_auto_upgrade_log(
            base_dir,
            "Skipping auto-upgrade; no canaries configured for this policy.",
        )
        return False

    now = now or timezone.now()
    target_type, target_value = _resolve_canary_target(repo_state, mode)
    if not target_type or not target_value:
        append_auto_upgrade_log(
            base_dir,
            "Skipping auto-upgrade; canary target could not be resolved.",
        )
        return False

    blockers: list[str] = []
    for node in canaries:
        live = _canary_is_live(node, now=now)
        matches_target = _canary_matches_target(node, target_type, target_value)
        if not (live and matches_target):
            blockers.append(
                _format_canary_state(
                    node,
                    live=live,
                    matches_target=matches_target,
                    target_type=target_type,
                    target_value=target_value,
                )
            )

    if blockers:
        append_auto_upgrade_log(
            base_dir,
            (
                "Skipping auto-upgrade; canary gate blocked. "
                f"Status: {'; '.join(blockers)}"
            ),
        )
        return False

    append_auto_upgrade_log(
        base_dir,
        "Canary gate satisfied; proceeding with auto-upgrade.",
    )
    return True

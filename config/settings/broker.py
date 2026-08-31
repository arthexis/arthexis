"""Broker URL resolution helpers shared across settings modules."""

from __future__ import annotations

import os
from pathlib import Path


def _resolve_node_role(node_role: str | None) -> str:
    """Resolve node role, falling back to role lock file when unspecified."""

    normalized = str(node_role or "").strip()
    if normalized:
        return normalized

    normalized = os.environ.get("NODE_ROLE", "").strip()
    if normalized:
        return normalized

    role_lock = Path(__file__).resolve().parents[2] / ".locks" / "role.lck"
    try:
        normalized = role_lock.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return "Terminal"
    return normalized or "Terminal"


def resolve_celery_broker_url(*, node_role: str | None = None) -> str:
    """Resolve the Celery broker URL with role-aware and legacy fallbacks."""

    explicit_broker_url = (
        os.environ.get("CELERY_BROKER_URL", "").strip()
        or os.environ.get("BROKER_URL", "").strip()
    )
    if explicit_broker_url:
        return explicit_broker_url

    if _resolve_node_role(node_role).lower() != "terminal":
        return "redis://localhost:6379/0"

    return "memory://localhost/"

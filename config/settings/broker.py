"""Broker URL resolution helpers shared across settings modules."""

from __future__ import annotations

import os
from pathlib import Path

REDIS_BROKER_SCHEMES = ("redis://", "rediss://", "unix://")


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


def resolve_redis_broker_fallback(*, node_role: str | None = None) -> str:
    """Return the Celery broker only when it is usable as a Redis endpoint."""

    broker_url = resolve_celery_broker_url(node_role=node_role)
    return broker_url if broker_url.startswith(REDIS_BROKER_SCHEMES) else ""

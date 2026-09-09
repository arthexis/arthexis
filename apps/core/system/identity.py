from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from config.roles import (
    SUPPORTED_ROLES,
    normalize_role,
    role_requires_shared_channel_layer,
)


def node_role() -> str:
    """Return the canonical Arthexis node role."""
    return normalize_role(getattr(settings, "NODE_ROLE", "Terminal"))


def version() -> str:
    """Return the repository VERSION value for the running checkout."""
    version_file = Path(settings.BASE_DIR) / "VERSION"
    return version_file.read_text(encoding="utf-8").strip()


def _channel_layer_is_healthy(role: str) -> bool:
    """Treat in-memory Channels as unhealthy only for multi-process roles."""

    if not role_requires_shared_channel_layer(role):
        return True

    decision = getattr(settings, "CHANNEL_LAYER_DECISION", None)
    backend = getattr(decision, "backend", "")
    if not backend:
        backend = (
            getattr(settings, "CHANNEL_LAYERS", {})
            .get("default", {})
            .get("BACKEND", "")
        )
    return backend != "channels.layers.InMemoryChannelLayer"


def status() -> str:
    """Return GOOD when core Arthexis application state is usable, else FAIL."""
    try:
        role = node_role()
        if role not in SUPPORTED_ROLES:
            return "FAIL"
        if not _channel_layer_is_healthy(role):
            return "FAIL"
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        executor = MigrationExecutor(connection)
        if executor.migration_plan(executor.loader.graph.leaf_nodes()):
            return "FAIL"
    except Exception:
        return "FAIL"
    return "GOOD"

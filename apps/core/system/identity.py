from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from config.roles import SUPPORTED_ROLES, normalize_role


def node_role() -> str:
    """Return the canonical Arthexis node role."""
    return normalize_role(getattr(settings, "NODE_ROLE", "Terminal"))


def version() -> str:
    """Return the repository VERSION value for the running checkout."""
    version_file = Path(settings.BASE_DIR) / "VERSION"
    return version_file.read_text(encoding="utf-8").strip()


def status() -> str:
    """Return GOOD when core Arthexis application state is usable, else FAIL."""
    try:
        if node_role() not in SUPPORTED_ROLES:
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

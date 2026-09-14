from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.core.system.identity import node_role, status as application_status, version
from apps.core.system.lifecycle_ownership import (
    LifecycleMode,
    OwnershipLayout,
    classify_managed_installation,
)

_SERVICE_KEYS_BY_ROLE = {
    "Terminal": ("web-local",),
    "Watchtower": ("web-local", "worker", "beat"),
    "Control": ("web-edge", "worker", "beat"),
    "Satellite": ("web-edge", "worker", "beat"),
}


def _managed_root(checkout: Path) -> Path:
    override = os.environ.get("ARTHEXIS_INSTALL_ROOT") or os.environ.get(
        "ARTHEXIS_MANAGED_ROOT"
    )
    if override:
        return Path(override).expanduser()
    if checkout.name == "app":
        return checkout.parent
    return checkout


def _git_value(checkout: Path, *arguments: str) -> str | None:
    if not (checkout / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _migration_state() -> tuple[bool | None, str | None]:
    try:
        executor = MigrationExecutor(connection)
        pending = bool(executor.migration_plan(executor.loader.graph.leaf_nodes()))
    except Exception as exc:
        return None, str(exc)
    return pending, None


def _service_state(unit: str) -> dict[str, str]:
    if shutil.which("systemctl") is None:
        return {"unit": unit, "active": "unavailable", "enabled": "unavailable"}

    def probe(action: str) -> str:
        result = subprocess.run(
            ["systemctl", action, unit],
            check=False,
            capture_output=True,
            text=True,
        )
        value = (result.stdout or result.stderr).strip().splitlines()
        return value[-1] if value else "unknown"

    return {
        "unit": unit,
        "active": probe("is-active"),
        "enabled": probe("is-enabled"),
    }


def inspect_lifecycle(checkout: str | Path | None = None) -> dict[str, Any]:
    """Return structured lifecycle diagnostics for the current Arthexis instance."""
    selected_checkout = Path(checkout or settings.BASE_DIR).expanduser()
    root = _managed_root(selected_checkout)
    ownership = classify_managed_installation(
        root,
        checkout_name=selected_checkout.name if selected_checkout.parent == root else "app",
    )
    layout: OwnershipLayout = ownership.layout

    revision = _git_value(selected_checkout, "rev-parse", "HEAD")
    dirty_output = _git_value(selected_checkout, "status", "--porcelain")
    dirty = bool(dirty_output) if dirty_output is not None else None

    role = node_role()
    pending_migrations, migration_error = _migration_state()
    health = application_status()

    services: list[dict[str, str]] = []
    if ownership.mode is LifecycleMode.MANAGED:
        for key in _SERVICE_KEYS_BY_ROLE.get(role, ()):
            services.append(_service_state(f"gway-arthexis-{key}.service"))

    database = settings.DATABASES.get("default", {})
    problems = list(ownership.problems)
    if ownership.mode is LifecycleMode.MANAGED:
        if not layout.environment.is_dir():
            problems.append("managed Python environment is missing")
        if pending_migrations is True:
            problems.append("database has pending migrations")
        if migration_error:
            problems.append(f"migration state unavailable: {migration_error}")
        for service in services:
            if service["active"] != "active":
                problems.append(f"service is not active: {service['unit']}")
        if health != "GOOD":
            problems.append("application health is not GOOD")

    if not ownership.valid:
        state = "invalid"
    elif ownership.mode is LifecycleMode.UNMANAGED:
        state = "unmanaged"
    elif problems:
        state = "degraded"
    else:
        state = "healthy"

    return {
        "mode": ownership.mode.value,
        "state": state,
        "valid": ownership.valid,
        "installation_id": ownership.installation_id,
        "root": str(layout.root),
        "checkout": str(selected_checkout),
        "environment": str(layout.environment),
        "persistent_data": str(layout.data),
        "settings_module": os.environ.get("DJANGO_SETTINGS_MODULE", "config.settings"),
        "version": version(),
        "revision": revision,
        "dirty": dirty,
        "role": role,
        "database": {
            "engine": database.get("ENGINE"),
            "name": str(database.get("NAME")) if database.get("NAME") is not None else None,
        },
        "pending_migrations": pending_migrations,
        "services": services,
        "application_health": health,
        "problems": problems,
    }


def render_lifecycle_status(report: dict[str, Any]) -> str:
    """Render lifecycle diagnostics for operators while preserving structured fields."""
    lines = [
        f"Lifecycle: {report['mode']} ({report['state']})",
        f"Version: {report['version']}",
        f"Revision: {report['revision'] or 'unknown'}",
        f"Role: {report['role']}",
        f"Root: {report['root']}",
        f"Checkout: {report['checkout']}",
        f"Environment: {report['environment']}",
        f"Persistent data: {report['persistent_data']}",
        f"Dirty managed checkout: {report['dirty']}",
        f"Pending migrations: {report['pending_migrations']}",
        f"Application health: {report['application_health']}",
    ]
    if report["services"]:
        lines.append("Services:")
        for service in report["services"]:
            lines.append(
                f"  {service['unit']}: active={service['active']} enabled={service['enabled']}"
            )
    if report["problems"]:
        lines.append("Problems:")
        lines.extend(f"  - {problem}" for problem in report["problems"])
    return "\n".join(lines)

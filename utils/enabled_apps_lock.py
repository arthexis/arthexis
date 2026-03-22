"""Helpers for persisting and loading enabled-application lock files."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, TypeAlias


ENABLED_APPS_LOCK_NAME = "enabled_apps.lck"
EnabledAppSelector: TypeAlias = str
EnabledAppsLockEntries: TypeAlias = set[EnabledAppSelector]


def get_enabled_apps_lock_path(base_dir: Path) -> Path:
    """Return the lock file path used to pin enabled local apps.

    Args:
        base_dir: Repository root where the lock directory is stored.

    Returns:
        The absolute path to the enabled-apps lock file.
    """

    return base_dir / ".locks" / ENABLED_APPS_LOCK_NAME


def read_enabled_apps_lock(base_dir: Path) -> EnabledAppsLockEntries | None:
    """Read enabled app selectors from disk.

    Args:
        base_dir: Repository root where the lock directory is stored.

    Returns:
        Parsed enabled app selectors, or ``None`` when no lock file exists so
        callers can keep default behavior with all manifest apps enabled.
    """

    lock_path = get_enabled_apps_lock_path(base_dir)
    if not lock_path.exists():
        return None

    entries: EnabledAppsLockEntries = {
        line.strip()
        for line in lock_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    return entries


def write_enabled_apps_lock(
    enabled_apps: Iterable[EnabledAppSelector], base_dir: Path
) -> Path:
    """Persist enabled app selectors to disk and return the written lock path.

    Args:
        enabled_apps: App selectors that should remain enabled.
        base_dir: Repository root where the lock directory is stored.

    Returns:
        The path to the written lock file.
    """

    lock_path = get_enabled_apps_lock_path(base_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    normalized = sorted(
        {name.strip() for name in enabled_apps if name and name.strip()}
    )
    payload = "\n".join(normalized)
    if payload:
        payload += "\n"

    lock_path.write_text(payload, encoding="utf-8")
    return lock_path

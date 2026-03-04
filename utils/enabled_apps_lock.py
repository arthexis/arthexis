"""Helpers for persisting and loading enabled-application lock files."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


ENABLED_APPS_LOCK_NAME = "enabled_apps.lck"


def get_enabled_apps_lock_path(base_dir: Path) -> Path:
    """Return the lock file path used to pin enabled local apps."""

    return base_dir / ".locks" / ENABLED_APPS_LOCK_NAME


def read_enabled_apps_lock(base_dir: Path) -> set[str] | None:
    """Read enabled app selectors from disk.

    Returns ``None`` when no lock file exists so callers can keep default
    behavior (all manifest apps enabled).
    """

    lock_path = get_enabled_apps_lock_path(base_dir)
    if not lock_path.exists():
        return None

    entries = {
        line.strip()
        for line in lock_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    return entries


def write_enabled_apps_lock(enabled_apps: Iterable[str], base_dir: Path) -> Path:
    """Persist enabled app selectors to disk and return the written lock path."""

    lock_path = get_enabled_apps_lock_path(base_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    normalized = sorted({name.strip() for name in enabled_apps if name and name.strip()})
    payload = "\n".join(normalized)
    if payload:
        payload += "\n"

    lock_path.write_text(payload, encoding="utf-8")
    return lock_path


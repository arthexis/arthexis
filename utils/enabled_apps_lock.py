"""Helpers for persisting and loading enabled-application lock files."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TypeAlias

ENABLED_APPS_LOCK_NAME = "enabled_apps.lck"
ENABLED_APPS_LOCK_DIRECT_PREFIX = "# direct:"
ENABLED_APPS_LOCK_DIRECT_SOURCE_PREFIX = "# direct-source:"
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

    entries: EnabledAppsLockEntries = set()
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        entry = line.strip().lstrip("\ufeff").strip()
        if entry and not entry.startswith("#"):
            entries.add(entry)
    return entries


def read_enabled_apps_lock_direct_entries(
    base_dir: Path,
) -> EnabledAppsLockEntries | None:
    """Read direct app selectors from generated lock metadata when present."""

    lock_path = get_enabled_apps_lock_path(base_dir)
    if not lock_path.exists():
        return None

    found_metadata = False
    entries: EnabledAppsLockEntries = set()
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        entry = line.strip().lstrip("\ufeff").strip()
        if not entry.startswith(ENABLED_APPS_LOCK_DIRECT_PREFIX):
            continue
        found_metadata = True
        direct_entry = entry.removeprefix(ENABLED_APPS_LOCK_DIRECT_PREFIX).strip()
        if direct_entry:
            entries.add(direct_entry)

    return entries if found_metadata else None


def read_enabled_apps_lock_direct_sources(base_dir: Path) -> dict[str, str]:
    """Read optional source labels for generated direct app metadata."""

    lock_path = get_enabled_apps_lock_path(base_dir)
    if not lock_path.exists():
        return {}

    sources: dict[str, str] = {}
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        entry = line.strip().lstrip("\ufeff").strip()
        if not entry.startswith(ENABLED_APPS_LOCK_DIRECT_SOURCE_PREFIX):
            continue
        source_entry = entry.removeprefix(
            ENABLED_APPS_LOCK_DIRECT_SOURCE_PREFIX
        ).strip()
        selector, _separator, source = source_entry.partition(" ")
        selector = selector.strip()
        source = source.strip()
        if selector and source:
            sources[selector] = source
    return sources


def write_enabled_apps_lock(
    enabled_apps: Iterable[EnabledAppSelector],
    base_dir: Path,
    *,
    direct_apps: Iterable[EnabledAppSelector] | None = None,
    direct_app_sources: Mapping[EnabledAppSelector, str] | None = None,
) -> Path:
    """Persist enabled app selectors to disk and return the written lock path.

    Args:
        enabled_apps: App selectors that should remain enabled.
        base_dir: Repository root where the lock directory is stored.
        direct_apps: Optional direct selectors used to distinguish explicit
            route-provider intent from dependency closure.
        direct_app_sources: Optional direct selector source labels used by
            lock refreshers to distinguish durable explicit intent from
            derived runtime lock intent.

    Returns:
        The path to the written lock file.
    """

    lock_path = get_enabled_apps_lock_path(base_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    normalized = sorted(
        {name.strip() for name in enabled_apps if name and name.strip()}
    )
    payload_lines: list[str] = []
    if direct_apps is not None:
        direct_normalized = sorted(
            {name.strip() for name in direct_apps if name and name.strip()}
        )
        source_map = {
            name.strip(): source.strip()
            for name, source in (direct_app_sources or {}).items()
            if name and name.strip() and source and source.strip()
        }
        payload_lines.append(
            "# Generated direct app selectors; normal lock readers ignore comments."
        )
        payload_lines.extend(
            f"{ENABLED_APPS_LOCK_DIRECT_PREFIX} {name}" for name in direct_normalized
        )
        payload_lines.extend(
            f"{ENABLED_APPS_LOCK_DIRECT_SOURCE_PREFIX} {name} {source_map[name]}"
            for name in direct_normalized
            if name in source_map
        )
        if normalized:
            payload_lines.append("")

    payload_lines.extend(normalized)
    payload = "\n".join(payload_lines)
    if payload:
        payload += "\n"

    lock_path.write_text(payload, encoding="utf-8")
    return lock_path

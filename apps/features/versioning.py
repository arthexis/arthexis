"""Suite feature baseline-version gating helpers."""

from __future__ import annotations

from packaging.version import InvalidVersion, Version

from utils.version import get_version


def _parse_version(raw: str | None) -> Version | None:
    """Return a parsed ``Version`` for ``raw`` when possible."""

    text = (raw or "").strip()
    if not text:
        return None
    text = text[1:] if text.lower().startswith("v") else text
    try:
        return Version(text)
    except InvalidVersion:
        return None


def current_suite_version() -> str:
    """Return the current local suite version from ``VERSION``."""

    return (get_version() or "").strip()


def is_baseline_version_reached(*, baseline_version: str | None, current_version: str | None) -> bool:
    """Return whether ``current_version`` meets or exceeds ``baseline_version``."""

    baseline = _parse_version(baseline_version)
    if baseline is None:
        return True

    current = _parse_version(current_version)
    if current is None:
        return False
    return current >= baseline


__all__ = [
    "current_suite_version",
    "is_baseline_version_reached",
]


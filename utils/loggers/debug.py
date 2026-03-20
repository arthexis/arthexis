"""Helpers for parsing debug logging settings."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Pattern


@dataclass(frozen=True)
class DebugLoggingControl:
    """Capture debug logging settings derived from the DEBUG environment value."""

    enabled: bool
    app_names: frozenset[str] | None
    app_regex: Pattern[str] | None

    def allows_app(self, app_name: str) -> bool:
        """Return True when the given app name is allowed to emit debug logs."""

        if self.enabled:
            return True

        normalized = app_name.strip().lower()
        if self.app_names is not None:
            return normalized in self.app_names

        if self.app_regex is not None:
            return self.app_regex.fullmatch(normalized) is not None

        return False


def parse_debug_logging(
    value: str | None, debug_enabled: bool = False
) -> DebugLoggingControl:
    """Return parsed debug logging settings for the DEBUG environment value."""

    if value is None or not value.strip():
        return DebugLoggingControl(debug_enabled, None, None)

    normalized = value.strip()
    lowered = normalized.lower()

    if lowered in {"1", "true", "yes", "on", "all", "*"}:
        return DebugLoggingControl(True, None, None)

    if lowered in {"0", "false", "no", "off"}:
        return DebugLoggingControl(False, None, None)

    if "," in normalized:
        apps = {
            entry.strip().lower()
            for entry in normalized.split(",")
            if entry.strip()
        }
        if apps:
            return DebugLoggingControl(False, frozenset(apps), None)
        return DebugLoggingControl(debug_enabled, None, None)

    try:
        pattern = re.compile(normalized, re.IGNORECASE)
    except re.error:
        return DebugLoggingControl(
            False, frozenset({normalized.lower()}), None
        )

    return DebugLoggingControl(False, None, pattern)

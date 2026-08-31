from __future__ import annotations

import os


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on", "all", "*"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def env_int(
    name: str,
    default: int,
    *,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    value = os.environ.get(name)
    if value is None:
        return default

    try:
        parsed = int(value.strip())
    except ValueError:
        return default

    if min_value is not None and parsed < min_value:
        return default
    if max_value is not None and parsed > max_value:
        return default
    return parsed

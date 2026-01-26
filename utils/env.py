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

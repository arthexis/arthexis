"""Parsing helpers for OCPP payload values."""

from __future__ import annotations

from typing import Any


def try_parse_int(value: Any) -> int | None:
    """Safely parse ``value`` as an integer, returning ``None`` when invalid."""

    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None

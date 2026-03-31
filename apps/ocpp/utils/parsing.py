"""Parsing helpers for OCPP payload values."""

from __future__ import annotations

from typing import SupportsIndex, SupportsInt, TypeAlias

ParseIntInput: TypeAlias = str | bytes | bytearray | SupportsInt | SupportsIndex | None


def try_parse_int(value: ParseIntInput) -> int | None:
    """Safely parse ``value`` as an integer, returning ``None`` when invalid."""

    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = ["ParseIntInput", "try_parse_int"]

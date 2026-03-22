from __future__ import annotations

from collections.abc import Iterable


def coerce_option_list(value) -> list[str]:
    """Normalize argparse/list-like option values into clean string lists."""

    if value is None:
        return []

    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, Iterable):
        candidates = list(value)
    else:
        candidates = [value]

    return [candidate.strip() for candidate in candidates if isinstance(candidate, str) and candidate.strip()]


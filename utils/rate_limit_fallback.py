from __future__ import annotations

import time

from django.core.cache import cache


def fallback_rate_limit_allows(
    *,
    scope_key: str,
    identifier: str | None,
    limit: int | None,
    window: int,
) -> bool:
    """Return whether an identifier remains inside a cache-backed fallback limit."""

    if not identifier or limit is None:
        return True

    cache_key = f"rate-limit:fallback:{scope_key}:{identifier}"
    now = time.time()
    payload = cache.get(cache_key)
    count = 0
    started_at = now

    if isinstance(payload, dict):
        count = int(payload.get("count", 0))
        started_at = float(payload.get("started_at", now))
        if window > 0 and now - started_at >= window:
            count = 0
            started_at = now
    elif payload is not None:
        try:
            count = int(payload)
            started_at = now
        except (TypeError, ValueError):
            count = 0
            started_at = now

    count += 1
    cache.set(
        cache_key,
        {"count": count, "started_at": started_at},
        timeout=window,
    )
    return count <= limit

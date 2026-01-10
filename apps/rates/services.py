from __future__ import annotations

from datetime import timedelta
import time
from typing import Callable

from django.core.cache import cache

from .models import RateLimit


class RateLimiter:
    """Evaluate and record activity against configured rate limits."""

    def __init__(
        self,
        *,
        target: object | None = None,
        scope_key: str = "default",
        identifier_builder: Callable[[], str | None] | None = None,
        fallback_limit: int | None = None,
        fallback_window: int = 60,
    ) -> None:
        self.target = target
        self.scope_key = scope_key
        self.identifier_builder = identifier_builder
        self.fallback_limit = fallback_limit
        self.fallback_window = fallback_window

    def _get_identifier(self, provided: str | None = None) -> str | None:
        if provided:
            return provided
        if self.identifier_builder:
            return self.identifier_builder()
        return None

    def _get_rule(self) -> RateLimit | None:
        return RateLimit.for_target(self.target, scope_key=self.scope_key)

    def _fallback_allowed(self, identifier: str) -> bool:
        if self.fallback_limit is None:
            return True
        cache_key = f"rate-limit:fallback:{self.scope_key}:{identifier}"
        now = time.time()
        payload = cache.get(cache_key)
        count = 0
        started_at = now

        if isinstance(payload, dict):
            count = int(payload.get("count", 0))
            started_at = float(payload.get("started_at", now))
            if self.fallback_window > 0 and now - started_at >= self.fallback_window:
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
            timeout=self.fallback_window,
        )
        return count <= self.fallback_limit

    def is_allowed(self, identifier: str | None = None) -> bool:
        """Return whether the identifier is within the configured rate limit."""

        resolved_identifier = self._get_identifier(identifier)
        if not resolved_identifier:
            return True

        rule = self._get_rule()
        if rule is None:
            return self._fallback_allowed(resolved_identifier)

        cache_key = rule.cache_key(resolved_identifier)
        timeout = rule.window_seconds
        if timeout <= 0 or rule.limit <= 0:
            return False

        added = cache.add(cache_key, 1, timeout=timeout)
        if added:
            return True

        try:
            current = cache.incr(cache_key)
        except ValueError:
            cache.set(cache_key, 1, timeout=timeout)
            return True
        return current <= rule.limit

    def remaining_time(self, identifier: str | None = None) -> timedelta | None:
        rule = self._get_rule()
        resolved_identifier = self._get_identifier(identifier)
        if not rule or not resolved_identifier:
            return None
        cache_key = rule.cache_key(resolved_identifier)
        expiry = cache.ttl(cache_key)
        if expiry is None:
            return None
        if expiry < 0:
            return timedelta(seconds=0)
        return timedelta(seconds=expiry)

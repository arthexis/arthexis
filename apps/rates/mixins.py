from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse
from django.views import View
from asgiref.sync import sync_to_async

from .services import RateLimiter


class RateLimitedViewMixin(View):
    """Mixin adding rate limiting checks to Django class-based views."""

    rate_limit_target: object | None = None
    rate_limit_scope: str = "default"
    rate_limit_fallback: int | None = None
    rate_limit_window: int = 60

    def get_rate_limit_identifier(self, request: HttpRequest) -> str | None:  # pragma: no cover - accessors
        return request.META.get("REMOTE_ADDR") or "unknown"

    def get_rate_limit_target(self):  # pragma: no cover - accessors
        return self.rate_limit_target

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any):
        limiter = RateLimiter(
            target=self.get_rate_limit_target(),
            scope_key=self.rate_limit_scope,
            fallback_limit=self.rate_limit_fallback,
            fallback_window=self.rate_limit_window,
        )
        identifier = self.get_rate_limit_identifier(request)
        if not limiter.is_allowed(identifier):
            return HttpResponse(status=429)
        return super().dispatch(request, *args, **kwargs)


class RateLimitedConsumerMixin:
    """Mixin for Channels consumers that enforces rate limits on connect."""

    rate_limit_target: object | None = None
    rate_limit_scope: str = "default"
    rate_limit_close_code: int = 4003
    rate_limit_fallback: int | None = None
    rate_limit_window: int = 60

    def get_rate_limit_identifier(self) -> str | None:  # pragma: no cover - accessors
        client = getattr(self, "client_ip", None)
        if client:
            return client
        scope_client = getattr(self, "scope", {}).get("client")
        if isinstance(scope_client, (list, tuple)) and scope_client:
            return scope_client[0]
        return "unknown"

    def get_rate_limit_target(self):  # pragma: no cover - accessors
        return self.rate_limit_target

    async def enforce_rate_limit(self) -> bool:
        limiter = RateLimiter(
            target=self.get_rate_limit_target(),
            scope_key=self.rate_limit_scope,
            fallback_limit=self.rate_limit_fallback,
            fallback_window=self.rate_limit_window,
        )
        identifier = self.get_rate_limit_identifier()
        allowed = await sync_to_async(limiter.is_allowed)(identifier)
        if not allowed:
            await self.handle_rate_limit_rejection(identifier)
            return False
        return True

    async def handle_rate_limit_rejection(self, identifier: str | None):  # pragma: no cover - override point
        await self.close(code=self.rate_limit_close_code)

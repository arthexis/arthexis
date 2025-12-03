from __future__ import annotations

from functools import wraps
from typing import Callable, TypeVar

from django.http import HttpRequest, HttpResponse

from .services import RateLimiter

F = TypeVar("F", bound=Callable[..., HttpResponse])


def rate_limited(
    *,
    target: object | None = None,
    scope_key: str = "default",
    identifier_getter: Callable[[HttpRequest, tuple, dict], str | None] | None = None,
    fallback_limit: int | None = None,
    fallback_window: int = 60,
) -> Callable[[F], F]:
    """Decorator enforcing configured rate limits for Django views."""

    def decorator(view_func: F) -> F:
        @wraps(view_func)
        def _wrapped(request: HttpRequest, *args, **kwargs):
            identifier = None
            if identifier_getter:
                identifier = identifier_getter(request, args, kwargs)
            else:
                identifier = request.META.get("REMOTE_ADDR")

            limiter = RateLimiter(
                target=target,
                scope_key=scope_key,
                fallback_limit=fallback_limit,
                fallback_window=fallback_window,
            )
            if not limiter.is_allowed(identifier):
                return HttpResponse(status=429)

            return view_func(request, *args, **kwargs)

        return _wrapped  # type: ignore[return-value]

    return decorator

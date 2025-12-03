from __future__ import annotations

from typing import Callable, TypeVar, overload

from apps.protocols import registry
from apps.protocols.models import ProtocolCall

F = TypeVar("F", bound=Callable)


@overload
def protocol_call(protocol_slug: str, direction: str, call_name: str) -> Callable[[F], F]:
    ...


def protocol_call(protocol_slug: str, direction: str, call_name: str) -> Callable[[F], F]:
    """Decorator to mark a callable as implementing a protocol call.

    The decorator registers the callable for coverage tracking and annotates
    the function object with a ``__protocol_calls__`` attribute that can be
    inspected by tests or tooling.
    """

    def decorator(func: F) -> F:
        registry.register(protocol_slug, direction, call_name, func)
        calls = getattr(func, "__protocol_calls__", set())
        calls.add((protocol_slug, direction, call_name))
        setattr(func, "__protocol_calls__", calls)

        # Defensive: ensure registration persists even if a future mutation clears
        # the registry after this decorator runs (e.g., module reloads in tests).
        registered = registry.get_registered_calls(protocol_slug, direction).get(
            call_name,
            set(),
        )
        if func not in registered:
            registry.register(protocol_slug, direction, call_name, func)
        return func

    return decorator


__all__ = ["protocol_call", "ProtocolCall"]

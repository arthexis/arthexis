"""Registry helpers for protocol call decorators and runtime lookups."""

from __future__ import annotations

from collections import defaultdict
from types import ModuleType
from typing import Callable, Iterable, Protocol, Sequence, TypeAlias

ProtocolHandler: TypeAlias = Callable[..., object]
ProtocolCallRegistration: TypeAlias = tuple[str, str, str]
ProtocolDirectionRegistry: TypeAlias = dict[str, set[ProtocolHandler]]
ProtocolSlugRegistry: TypeAlias = dict[str, ProtocolDirectionRegistry]
ProtocolCallRegistry: TypeAlias = dict[str, ProtocolSlugRegistry]


class SupportsProtocolCalls(Protocol):
    """Protocol for callables annotated by ``@protocol_call`` metadata."""

    __protocol_calls__: Sequence[ProtocolCallRegistration]


_registry: ProtocolCallRegistry = defaultdict(
    lambda: defaultdict(lambda: defaultdict(set))
)


def register(
    protocol_slug: str, direction: str, call_name: str, fn: ProtocolHandler
) -> None:
    """Register a callable for a protocol/direction/call combination.

    Args:
        protocol_slug: Protocol slug such as ``ocpp16``.
        direction: Message direction identifier.
        call_name: Protocol call name.
        fn: Callable handling the protocol call.

    Returns:
        None.
    """

    normalized_slug = protocol_slug.strip()
    normalized_direction = direction.strip()
    normalized_call = call_name.strip()
    _registry[normalized_slug][normalized_direction][normalized_call].add(fn)


def get_registered_calls(
    protocol_slug: str, direction: str | None = None
) -> dict[str, set[ProtocolHandler]]:
    """Return registered call handlers for a protocol, optionally filtered by direction.

    Args:
        protocol_slug: Protocol slug used as the top-level registry key.
        direction: Optional direction to filter by.

    Returns:
        A mapping of call names to registered handler sets.
    """

    protocol_entry = _registry.get(protocol_slug, {})
    if direction is None:
        merged: dict[str, set[ProtocolHandler]] = defaultdict(set)
        for calls in protocol_entry.values():
            for name, funcs in calls.items():
                merged[name].update(funcs)
        return dict(merged)
    return dict(protocol_entry.get(direction, {}))


def clear_registry() -> None:
    """Remove all registered protocol call handlers from the in-memory registry."""

    _registry.clear()


def iter_registered_protocols() -> Iterable[tuple[str, str, str, ProtocolHandler]]:
    """Yield each registered protocol mapping as a flattened tuple sequence.

    Returns:
        Tuples of ``(protocol_slug, direction, call_name, handler)``.
    """

    for slug, directions in _registry.items():
        for direction, calls in directions.items():
            for name, callables in calls.items():
                for fn in callables:
                    yield slug, direction, name, fn


def rehydrate_from_module(module: ModuleType) -> None:
    """Re-register protocol call annotations found on module attributes.

    Args:
        module: Imported module whose contents may expose ``__protocol_calls__``.

    Returns:
        None.
    """

    def _rehydrate(obj: object) -> None:
        maybe_protocol_calls = getattr(obj, "__protocol_calls__", None)
        if maybe_protocol_calls is None:
            return
        annotated = maybe_protocol_calls
        if not isinstance(annotated, Sequence):
            return
        if not callable(obj):
            return
        for slug, direction, name in annotated:
            register(slug, direction, name, obj)

    for attr in module.__dict__.values():
        _rehydrate(attr)
        if isinstance(attr, type):
            for klass in attr.__mro__:
                for member in klass.__dict__.values():
                    _rehydrate(member)


__all__ = [
    "clear_registry",
    "get_registered_calls",
    "iter_registered_protocols",
    "rehydrate_from_module",
    "register",
]

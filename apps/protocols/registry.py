from __future__ import annotations

from collections import defaultdict
from typing import Callable, Iterable

ProtocolCallRegistry = dict[str, dict[str, dict[str, set[Callable]]]]


_registry: ProtocolCallRegistry = defaultdict(
    lambda: defaultdict(lambda: defaultdict(set))
)


def register(protocol_slug: str, direction: str, call_name: str, fn: Callable) -> None:
    normalized_slug = protocol_slug.strip()
    normalized_direction = direction.strip()
    normalized_call = call_name.strip()
    _registry[normalized_slug][normalized_direction][normalized_call].add(fn)


def get_registered_calls(protocol_slug: str, direction: str | None = None) -> dict[str, set[Callable]]:
    protocol_entry = _registry.get(protocol_slug, {})
    if direction is None:
        merged: dict[str, set[Callable]] = defaultdict(set)
        for dir_key, calls in protocol_entry.items():
            for name, funcs in calls.items():
                merged[name].update(funcs)
        return dict(merged)
    return dict(protocol_entry.get(direction, {}))


def clear_registry() -> None:
    _registry.clear()


def iter_registered_protocols() -> Iterable[tuple[str, str, str, Callable]]:
    for slug, directions in _registry.items():
        for direction, calls in directions.items():
            for name, callables in calls.items():
                for fn in callables:
                    yield slug, direction, name, fn


__all__ = [
    "register",
    "get_registered_calls",
    "iter_registered_protocols",
    "clear_registry",
]

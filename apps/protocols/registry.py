from __future__ import annotations

from collections import defaultdict
from types import ModuleType
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


def rehydrate_from_module(module: ModuleType) -> None:
    """Re-register any protocol call annotations found on module attributes."""

    def _rehydrate(obj: object) -> None:
        calls = getattr(obj, "__protocol_calls__", None)
        if not calls:
            return
        for slug, direction, name in calls:
            register(slug, direction, name, obj)

    for attr in module.__dict__.values():
        _rehydrate(attr)
        if isinstance(attr, type):
            for member in attr.__dict__.values():
                _rehydrate(member)


__all__ = [
    "register",
    "get_registered_calls",
    "iter_registered_protocols",
    "rehydrate_from_module",
    "clear_registry",
]

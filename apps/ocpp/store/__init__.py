"""In-memory store for OCPP data with file backed logs."""

from __future__ import annotations

from typing import Any

from . import logs, pending_calls, scheduler, state, transactions

_modules = (state, logs, pending_calls, transactions, scheduler)

for module in _modules:
    for name in getattr(module, "__all__", []):
        globals()[name] = getattr(module, name)


def reassign_identity(old_key: str, new_key: str) -> str:
    """Move any stored data from ``old_key`` to ``new_key``."""

    if old_key == new_key:
        return new_key
    if not old_key:
        return new_key
    for mapping in (state.connections, state.transactions, logs.history):
        if old_key in mapping:
            mapping[new_key] = mapping.pop(old_key)
    for log_type in logs.logs:
        store_map = logs.logs[log_type]
        if old_key in store_map:
            store_map[new_key] = store_map.pop(old_key)
    for log_type in logs.log_names:
        names = logs.log_names[log_type]
        if old_key in names:
            names[new_key] = names.pop(old_key)
    return new_key


__all__ = [
    name for module in _modules for name in getattr(module, "__all__", [])
] + [
    "reassign_identity",
]


def __getattr__(name: str) -> Any:
    for module in _modules:
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

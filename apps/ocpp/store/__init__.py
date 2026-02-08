"""In-memory store for OCPP data with file backed logs."""

from __future__ import annotations

from . import logs, pending_calls, scheduler, state, transactions

_modules = (state, logs, pending_calls, transactions, scheduler)

for module in _modules:
    for name in getattr(module, "__all__", []):
        globals()[name] = getattr(module, name)

__all__ = [name for module in _modules for name in getattr(module, "__all__", [])]


def __getattr__(name: str) -> Any:
    for module in _modules:
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

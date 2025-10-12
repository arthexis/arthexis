"""Packaging utilities for gway release integration."""

from __future__ import annotations

from . import package as _package

__all__ = list(getattr(_package, "__all__", ()))


def __getattr__(name: str):
    if name in __all__:
        return getattr(_package, name)
    raise AttributeError(name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

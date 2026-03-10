"""Local app package public API.

Importing from :mod:`apps.locals` provides the same symbols as
:mod:`apps.locals.api` while deferring heavy imports until first use.
"""

from importlib import import_module

from ._exports import LOCALS_PUBLIC_API_EXPORTS

__all__ = LOCALS_PUBLIC_API_EXPORTS


def __getattr__(name: str):
    if name in __all__:
        return getattr(import_module("apps.locals.api"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))

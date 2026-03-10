"""Backward-compatible imports for local user data helpers."""

from importlib import import_module

from apps.locals._exports import LOCALS_PUBLIC_API_EXPORTS
from apps.locals.seeds import load_local_seed_zips
from apps.locals.user_fixtures import load_shared_user_fixtures, load_user_fixtures

__all__ = LOCALS_PUBLIC_API_EXPORTS


def __getattr__(name: str):
    if name in __all__:
        return getattr(import_module("apps.locals.api"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))

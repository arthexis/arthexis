"""Expose release build helpers for the gateway packaging commands."""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

__all__ = [
    "Credentials",
    "GitCredentials",
    "Package",
    "RepositoryTarget",
    "DEFAULT_PACKAGE",
    "ReleaseError",
    "TestsFailed",
    "build",
    "promote",
    "run_tests",
]

_HELPER_MODULE_NAME = "core.release"
_helper_module: ModuleType | None = None


def _load_helper() -> ModuleType:
    """Return the underlying release helper module.

    When the module is executed as a script, ``__package__`` may be empty,
    making relative imports unreliable. In that case fall back to an absolute
    import using the fully-qualified helper module name.
    """

    global _helper_module
    if _helper_module is None:
        if not __package__:
            helper = importlib.import_module(_HELPER_MODULE_NAME)
        else:
            helper = importlib.import_module(_HELPER_MODULE_NAME)
        _helper_module = helper
    return _helper_module


def _ensure_exports() -> None:
    helper = _load_helper()
    for name in __all__:
        globals()[name] = getattr(helper, name)


def __getattr__(name: str) -> Any:
    if name in __all__:
        helper = _load_helper()
        try:
            return getattr(helper, name)
        except AttributeError as exc:  # pragma: no cover - propagate missing attrs
            raise AttributeError(name) from exc
    raise AttributeError(name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


_ensure_exports()

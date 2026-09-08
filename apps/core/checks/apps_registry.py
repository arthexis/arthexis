"""Lazy compatibility access to application-registry Django checks."""

from importlib import import_module

_IMPL_MODULE = "apps.app.checks.apps_registry"


def __getattr__(name: str):
    module = import_module(_IMPL_MODULE)
    try:
        return getattr(module, name)
    except AttributeError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc


def __dir__() -> list[str]:
    module = import_module(_IMPL_MODULE)
    return sorted(set(globals()) | set(dir(module)))

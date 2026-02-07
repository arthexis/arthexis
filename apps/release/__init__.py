from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["release"]


def __getattr__(name: str) -> Any:
    if name == "release":
        module = import_module(".release", __name__)
        globals()["release"] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + ["release"])

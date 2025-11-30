"""Screenshot specification registry and discovery utilities."""

from __future__ import annotations

import importlib
import pkgutil

from .base import (
    ScreenshotContext,
    ScreenshotResult,
    ScreenshotSpec,
    ScreenshotSpecRunner,
    ScreenshotUnavailable,
    registry,
)

__all__ = [
    "ScreenshotContext",
    "ScreenshotResult",
    "ScreenshotSpec",
    "ScreenshotSpecRunner",
    "ScreenshotUnavailable",
    "autodiscover",
    "registry",
]

_DISCOVERED = False


def autodiscover() -> None:
    """Import all concrete spec modules under :mod:`apps.pages.screenshot_specs`."""

    global _DISCOVERED
    if _DISCOVERED:
        return
    package_name = __name__
    for module in pkgutil.iter_modules(__path__):  # type: ignore[name-defined]
        if module.name.startswith("_") or module.name == "base":
            continue
        importlib.import_module(f"{package_name}.{module.name}")
    _DISCOVERED = True

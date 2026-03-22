"""Top-level package for the Arthexis distribution.

This module provides a stable import target for environments that install the
repository as the ``arthexis`` Python distribution, including editable installs.
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def _resolve_version() -> str:
    """Return the installed package version when available.

    Returns:
        Installed distribution version, or ``"0+unknown"`` when package
        metadata is unavailable.
    """

    try:
        return version("arthexis")
    except PackageNotFoundError:
        return "0+unknown"


__all__ = ["__version__"]
__version__ = _resolve_version()

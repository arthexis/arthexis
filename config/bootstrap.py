"""Shared process bootstrap for Django-aware entrypoints."""

from __future__ import annotations

from config.loadenv import loadenv
from config.sqlite_driver import bootstrap_sqlite_driver

_bootstrapped = False


def bootstrap_django_environment() -> None:
    """Load Arthexis process prerequisites before Django initializes."""
    global _bootstrapped
    if _bootstrapped:
        return

    loadenv()
    bootstrap_sqlite_driver()
    _bootstrapped = True

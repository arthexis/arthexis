"""SQLite driver bootstrap helpers.

This module allows selecting the SQLite module implementation before Django
initializes database connections.
"""

from __future__ import annotations

import importlib
import os
import sys
import warnings


def bootstrap_sqlite_driver() -> None:
    """Select and install the requested SQLite driver module.

    The selection is controlled by ``ARTHEXIS_SQLITE_DRIVER``. Supported values:

    - ``stdlib`` (default): keep Python's built-in ``sqlite3`` module.
    - ``pysqlite3``: prefer ``pysqlite3`` and alias it to ``sqlite3``.

    When ``pysqlite3`` is requested but unavailable, this function emits a
    warning and leaves the standard library driver in place.
    """

    selected_driver = os.environ.get("ARTHEXIS_SQLITE_DRIVER", "stdlib").strip().lower()
    if not selected_driver or selected_driver == "stdlib":
        return

    if selected_driver != "pysqlite3":
        warnings.warn(
            "Unsupported ARTHEXIS_SQLITE_DRIVER value "
            f"{selected_driver!r}; using stdlib sqlite3.",
            stacklevel=2,
        )
        return

    try:
        pysqlite3 = importlib.import_module("pysqlite3")
    except ModuleNotFoundError:
        warnings.warn(
            "ARTHEXIS_SQLITE_DRIVER is set to 'pysqlite3' but pysqlite3 is not "
            "installed; using stdlib sqlite3.",
            stacklevel=2,
        )
        return

    sys.modules["sqlite3"] = pysqlite3

"""Helper utilities for the ``env-refresh`` maintenance script."""

from __future__ import annotations

import re
import time
from pathlib import Path

from django.conf import settings
from django.db import connections


__all__ = ["is_sqlite_corruption_error", "unlink_sqlite_db"]


def unlink_sqlite_db(path: Path) -> None:
    """Close database connections and remove only the SQLite DB file."""

    connections.close_all()
    try:
        base_dir = Path(settings.BASE_DIR).resolve()
    except Exception:
        base_dir = path.parent.resolve()
    path = path.resolve()
    try:
        path.relative_to(base_dir)
    except ValueError:
        raise RuntimeError(f"Refusing to delete database outside {base_dir}: {path}")
    if not re.fullmatch(r"(?:test_)?db(?:_[0-9a-f]{6})?\.sqlite3", path.name):
        raise RuntimeError(f"Refusing to delete unexpected database file: {path.name}")
    for _ in range(5):
        try:
            path.unlink(missing_ok=True)
            break
        except PermissionError:
            time.sleep(0.1)
            connections.close_all()


def is_sqlite_corruption_error(exc: BaseException) -> bool:
    """Return ``True`` when *exc* looks like a SQLite corruption error."""

    message = str(exc).lower()
    patterns = (
        "database disk image is malformed",
        "file is not a database",
        "file is encrypted or is not a database",
    )
    return any(pattern in message for pattern in patterns)

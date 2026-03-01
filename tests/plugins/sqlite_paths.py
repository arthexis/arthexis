"""Pytest bootstrap helpers for stable SQLite test database paths."""

from __future__ import annotations

import atexit
import os
import tempfile
from pathlib import Path

_PYTEST_SQLITE_TMP_DIR: tempfile.TemporaryDirectory[str] | None = None



def sqlite_path_is_writable(path_value: str) -> bool:
    """Return ``True`` when the SQLite path parent directory accepts writes."""

    candidate = Path(path_value).expanduser()
    parent = candidate.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=parent):
            pass
    except OSError:
        return False
    return True


def sqlite_uses_special_name(path_value: str) -> bool:
    """Return ``True`` for SQLite values that are not filesystem paths."""

    value = path_value.strip()
    return value == ":memory:" or value.startswith("file:")


def set_writable_sqlite_env(var_name: str, fallback: Path) -> None:
    """Set SQLite env vars to writable paths while preserving valid caller overrides."""

    configured = os.environ.get(var_name)
    if configured and (sqlite_uses_special_name(configured) or sqlite_path_is_writable(configured)):
        return
    os.environ[var_name] = str(fallback)


def configure_ephemeral_sqlite_paths() -> None:
    """Route SQLite DBs to writable temporary paths for stable pytest setup."""

    global _PYTEST_SQLITE_TMP_DIR
    _PYTEST_SQLITE_TMP_DIR = tempfile.TemporaryDirectory(prefix=f"arthexis-pytest-{os.getpid()}-")
    atexit.register(_PYTEST_SQLITE_TMP_DIR.cleanup)
    db_root = Path(_PYTEST_SQLITE_TMP_DIR.name)
    set_writable_sqlite_env("ARTHEXIS_SQLITE_PATH", db_root / "default.sqlite3")
    set_writable_sqlite_env("ARTHEXIS_SQLITE_TEST_PATH", db_root / "test.sqlite3")


def ensure_clean_test_databases(base_dir: Path) -> None:
    """Remove stale SQLite test database files from common local locations."""

    candidates = [
        base_dir / "test_db.sqlite3",
        base_dir / "work" / "test_db.sqlite3",
        base_dir / "work" / "test_db" / "test_db.sqlite3",
    ]

    for path in candidates:
        if path.exists():
            path.unlink()

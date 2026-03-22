"""Pytest bootstrap helpers for stable SQLite test database paths."""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

_PYTEST_SQLITE_TMP_DIR: Path | None = None

def pytest_worker_suffix() -> str:
    """Return a worker-specific suffix for SQLite file names under xdist."""

    return os.path.basename(os.environ.get("PYTEST_XDIST_WORKER") or "main")


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
    if _PYTEST_SQLITE_TMP_DIR is not None:
        db_root = _PYTEST_SQLITE_TMP_DIR
    else:
        db_root = Path(tempfile.mkdtemp(prefix=f"arthexis-pytest-{os.getpid()}-"))
        _PYTEST_SQLITE_TMP_DIR = db_root
        atexit.register(lambda root=db_root: shutil.rmtree(root, ignore_errors=True))

    worker_suffix = pytest_worker_suffix()
    set_writable_sqlite_env("ARTHEXIS_SQLITE_PATH", db_root / f"default-{worker_suffix}.sqlite3")
    set_writable_sqlite_env("ARTHEXIS_SQLITE_TEST_PATH", db_root / f"test-{worker_suffix}.sqlite3")


def ensure_clean_test_databases(base_dir: Path) -> None:
    """Remove stale SQLite test database files from common local locations."""

    candidates = [
        base_dir / "test_db.sqlite3",
        base_dir / "work" / "test_db.sqlite3",
        base_dir / "work" / "test_db" / "test_db.sqlite3",
        Path("/dev/shm") / "arthexis" / "test_db.sqlite3",
        Path(tempfile.gettempdir()) / "arthexis" / "test_db.sqlite3",
    ]

    for path in candidates:
        _remove_sqlite_artifacts(path)


def _remove_sqlite_artifacts(db_path: Path) -> None:
    """Best-effort removal of SQLite database files and sidecar artifacts."""

    artifacts = [
        db_path,
        Path(f"{db_path}-shm"),
        Path(f"{db_path}-wal"),
        Path(f"{db_path}-journal"),
    ]

    for artifact in artifacts:
        try:
            if not artifact.exists():
                continue
            if artifact.is_dir():
                shutil.rmtree(artifact, ignore_errors=True)
                continue
            artifact.unlink(missing_ok=True)
        except OSError:
            continue

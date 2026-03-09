"""Tests for SQLite pytest path bootstrapping helpers."""

from __future__ import annotations

from pathlib import Path

from tests.plugins import sqlite_paths


def test_ensure_clean_test_databases_removes_sidecars(tmp_path: Path) -> None:
    """Regression: cleanup should remove stale SQLite sidecar files too."""

    db_dir = tmp_path / "work" / "test_db"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "test_db.sqlite3"

    for suffix in ("", "-wal", "-shm", "-journal"):
        (Path(f"{db_path}{suffix}")).write_text("stale")

    sqlite_paths.ensure_clean_test_databases(tmp_path)

    for suffix in ("", "-wal", "-shm", "-journal"):
        assert not Path(f"{db_path}{suffix}").exists()

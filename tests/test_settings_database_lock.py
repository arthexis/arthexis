from __future__ import annotations

import json

from config.settings_helpers import load_database_lock


def test_load_database_lock_reads_postgres_payload(tmp_path):
    """Valid postgres lock payloads should be returned as string mappings."""

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "postgres.lck").write_text(
        json.dumps(
            {
                "backend": "postgres",
                "name": "appdb",
                "user": "app",
                "password": "secret",
                "host": "db",
                "port": 5432,
            }
        ),
        encoding="utf-8",
    )

    config = load_database_lock(tmp_path)

    assert config is not None
    assert config["backend"] == "postgres"
    assert config["port"] == "5432"


def test_load_database_lock_ignores_invalid_backend(tmp_path):
    """Non-postgres lock payloads should be ignored."""

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "postgres.lck").write_text(
        json.dumps({"backend": "sqlite", "name": "db.sqlite3"}), encoding="utf-8"
    )

    assert load_database_lock(tmp_path) is None

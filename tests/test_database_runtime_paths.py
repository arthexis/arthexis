from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SETTINGS_SCRIPT = r"""
import json
from pathlib import Path

import config.settings.database as database

print(json.dumps({
    "sqlite": str(database.SQLITE_DB_PATH),
    "external": str(database._database_data_path("dbs", database.BASE_DIR / "work" / "dbs")),
}, sort_keys=True))
"""


def _load_database_paths(
    tmp_path: Path,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    for key in (
        "ARTHEXIS_DATA_DIR",
        "ARTHEXIS_MODE",
        "ARTHEXIS_SQLITE_PATH",
        "ARTHEXIS_SQLITE_TEST_PATH",
    ):
        env.pop(key, None)
    env["DJANGO_SECRET_KEY"] = "test-secret"
    env["ARTHEXIS_SQLITE_TEST_PATH"] = str(tmp_path / "test.sqlite3")
    env.update(extra_env or {})

    result = subprocess.run(
        [sys.executable, "-c", SETTINGS_SCRIPT],
        cwd=repo_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_checkout_mode_preserves_existing_database_locations(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]

    paths = _load_database_paths(tmp_path)

    assert paths == {
        "external": str(repo_root / "work" / "dbs"),
        "sqlite": str(repo_root / "db.sqlite3"),
    }


def test_installed_mode_uses_arthexis_data_dir(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"

    paths = _load_database_paths(
        tmp_path,
        {
            "ARTHEXIS_MODE": "installed",
            "ARTHEXIS_DATA_DIR": str(data_dir),
        },
    )

    assert paths == {
        "external": str(data_dir / "dbs"),
        "sqlite": str(data_dir / "db.sqlite3"),
    }


def test_checkout_can_explicitly_redirect_database_state(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"

    paths = _load_database_paths(
        tmp_path,
        {"ARTHEXIS_DATA_DIR": str(data_dir)},
    )

    assert paths == {
        "external": str(data_dir / "dbs"),
        "sqlite": str(data_dir / "db.sqlite3"),
    }


def test_specific_sqlite_override_keeps_precedence(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    sqlite_path = tmp_path / "custom.sqlite3"

    paths = _load_database_paths(
        tmp_path,
        {
            "ARTHEXIS_MODE": "installed",
            "ARTHEXIS_DATA_DIR": str(data_dir),
            "ARTHEXIS_SQLITE_PATH": str(sqlite_path),
        },
    )

    assert paths["sqlite"] == str(sqlite_path)
    assert paths["external"] == str(data_dir / "dbs")

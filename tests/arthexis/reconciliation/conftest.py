from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from arthexis.reconciliation.capture import capture_legacy_installation
from arthexis.reconciliation.fixture import restore_fixture

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.fixture(scope="session")
def reconciled_e2e_baseline(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    root = tmp_path_factory.mktemp("reconciled-e2e-baseline")
    legacy = root / "legacy"
    legacy.mkdir()
    database = legacy / "db.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE django_migrations "
            "(id INTEGER PRIMARY KEY, app TEXT, name TEXT, applied TEXT)"
        )
        connection.execute(
            "INSERT INTO django_migrations(app, name, applied) "
            "VALUES ('core', '0001_initial', '2024-01-01')"
        )
        connection.execute(
            "CREATE TABLE core_rfid "
            "(id INTEGER PRIMARY KEY, rfid TEXT, active INTEGER)"
        )
        connection.execute(
            "INSERT INTO core_rfid(rfid, active) VALUES ('legacy-card', 1)"
        )

    capture = capture_legacy_installation(legacy, root / "captures")
    fixture = restore_fixture(capture.path, root / "fixtures")
    source_sha = _sha256(fixture.database_path)

    environment = os.environ.copy()
    environment["ARTHEXIS_DATA_DIR"] = str(root / "current-data")
    environment.pop("ARTHEXIS_DATABASE_PATH", None)
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "reconcile.py"),
            "reconcile-fixture",
            str(fixture.path),
            "--nice",
            "0",
            "--batch-size",
            "1",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)

    return {
        "root": root,
        "capture_path": capture.path,
        "fixture_path": fixture.path,
        "source_database": fixture.database_path,
        "source_sha256": source_sha,
        "result": result,
    }

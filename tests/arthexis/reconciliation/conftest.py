from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _legacy_installation(root: Path) -> Path:
    root.mkdir()
    database = root / "db.sqlite3"
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
    (root / "VERSION").write_text("0.9.0\n", encoding="utf-8")
    return database


@pytest.fixture(scope="session")
def reconciled_e2e_baseline(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    root = tmp_path_factory.mktemp("rehearsal-e2e-baseline")
    legacy = root / "legacy"
    source_database = _legacy_installation(legacy)
    source_sha = _sha256(source_database)
    output = root / "rehearsal"

    environment = os.environ.copy()
    environment["ARTHEXIS_DATA_DIR"] = str(root / "current-data")
    environment.pop("ARTHEXIS_DATABASE_PATH", None)
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "reconcile.py"),
            "rehearse",
            str(legacy),
            "--output",
            str(output),
            "--nice",
            "0",
            "--batch-size",
            "1",
            "--cutover",
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
        "legacy_path": legacy,
        "source_database": source_database,
        "source_sha256": source_sha,
        "rehearsal_root": output,
        "capture_path": Path(str(result["capture"]["path"])),
        "fixture_path": Path(str(result["fixture"]["path"])),
        "destination_database": Path(str(result["destination_database"])),
        "resource_report": Path(str(result["resource_report"])),
        "json_report": Path(str(result["json_report"])),
        "text_report": Path(str(result["text_report"])),
        "go_bundle": Path(str(result["go_bundle"])),
        "result": result,
    }

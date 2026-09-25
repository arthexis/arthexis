"""Tests for passive legacy installation capture."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from arthexis.reconciliation.capture import (
    CAPTURE_FORMAT,
    capture_legacy_installation,
    discover_legacy_installation,
    verify_capture,
)


def _legacy_installation(root: Path) -> Path:
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
        connection.execute("CREATE TABLE core_rfid (id INTEGER PRIMARY KEY, rfid TEXT)")
        connection.execute("INSERT INTO core_rfid(rfid) VALUES ('secret-card-id')")
    (root / "VERSION").write_text("1.7.3\n", encoding="utf-8")
    (root / "requirements.txt").write_text("Django==4.2\n", encoding="utf-8")
    (root / ".env").write_text("PRIVATE_TOKEN=do-not-export\n", encoding="utf-8")
    return database


def test_discovers_passive_installation_and_fingerprints_config(tmp_path):
    database = _legacy_installation(tmp_path)

    discovered = discover_legacy_installation(tmp_path)

    assert discovered.database == database.resolve()
    assert discovered.version == "1.7.3"
    assert discovered.configuration_inventory[0]["path"] == ".env"
    assert discovered.configuration_inventory[0]["content_exported"] is False


def test_capture_uses_verified_snapshot_without_exporting_secret_config(tmp_path):
    source = tmp_path / "legacy"
    source.mkdir()
    database = _legacy_installation(source)
    before = database.read_bytes()
    destination = tmp_path / "current-data" / "migration" / "captures"

    result = capture_legacy_installation(
        source,
        destination,
        now=datetime(2026, 9, 25, 13, 45, tzinfo=timezone.utc),
    )

    assert database.read_bytes() == before
    assert result.path.parent == destination.resolve()
    assert result.database_path.is_file()
    assert not (result.path / ".env").exists()
    assert not (result.path / "metadata" / ".env").exists()

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["format"] == CAPTURE_FORMAT
    assert manifest["source"]["version"] == "1.7.3"
    assert manifest["database"]["snapshot_method"] == "sqlite-online-backup"
    assert manifest["database"]["integrity"] == "ok"
    assert manifest["source"]["configuration_inventory"][0]["path"] == ".env"
    assert any("not exported" in warning for warning in manifest["warnings"])
    assert (result.path / "metadata" / "VERSION").read_text().strip() == "1.7.3"

    verification = verify_capture(result.path)
    assert verification["verified"] is True
    assert verification["capture_id"] == result.capture_id


def test_verify_rejects_tampered_capture(tmp_path):
    source = tmp_path / "legacy"
    source.mkdir()
    _legacy_installation(source)
    result = capture_legacy_installation(
        source,
        tmp_path / "captures",
        now=datetime(2026, 9, 25, 13, 46, tzinfo=timezone.utc),
    )

    with result.database_path.open("ab") as handle:
        handle.write(b"tamper")

    with pytest.raises(ValueError, match="checksum"):
        verify_capture(result.path)


def test_capture_refuses_nonlegacy_database(tmp_path):
    source = tmp_path / "new"
    source.mkdir()
    with sqlite3.connect(source / "db.sqlite3") as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")

    with pytest.raises(ValueError, match="legacy"):
        capture_legacy_installation(source, tmp_path / "captures")

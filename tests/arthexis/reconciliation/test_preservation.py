"""Tests for repository-safe preserved legacy capture bundles."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from arthexis.reconciliation.capture import capture_legacy_installation, verify_capture
from arthexis.reconciliation.preservation import (
    PRESERVATION_FORMAT,
    preserve_capture,
)


def _legacy_installation(root: Path, *, with_secret: bool = False) -> Path:
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
            "(id INTEGER PRIMARY KEY, rfid TEXT, ocpp_id_tag TEXT, custom_label TEXT)"
        )
        connection.execute(
            "INSERT INTO core_rfid(rfid, ocpp_id_tag, custom_label) "
            "VALUES ('REAL-CARD-123', 'REAL-TAG-123', 'Alice card')"
        )
        connection.execute(
            "CREATE TABLE cards_rfidattempt "
            "(id INTEGER PRIMARY KEY, rfid TEXT, status TEXT)"
        )
        connection.execute(
            "INSERT INTO cards_rfidattempt(rfid, status) "
            "VALUES ('REAL-CARD-123', 'Accepted')"
        )
        connection.execute(
            "CREATE TABLE core_account "
            "(id INTEGER PRIMARY KEY, name TEXT, ocpp_id_tag TEXT)"
        )
        connection.execute(
            "INSERT INTO core_account(name, ocpp_id_tag) "
            "VALUES ('Alice Example', 'REAL-TAG-123')"
        )
        connection.execute(
            "CREATE TABLE ocpp_charger "
            "(id INTEGER PRIMARY KEY, charger_id TEXT)"
        )
        connection.execute(
            "INSERT INTO ocpp_charger(charger_id) VALUES ('FIELD-CHARGER-01')"
        )
        connection.execute(
            "CREATE TABLE ocpp_transaction "
            "(id INTEGER PRIMARY KEY, charger_id INTEGER, id_tag TEXT)"
        )
        connection.execute(
            "INSERT INTO ocpp_transaction(charger_id, id_tag) "
            "VALUES (1, 'REAL-TAG-123')"
        )
        if with_secret:
            connection.execute(
                "CREATE TABLE legacy_credentials "
                "(id INTEGER PRIMARY KEY, api_key TEXT)"
            )
            connection.execute(
                "INSERT INTO legacy_credentials(api_key) VALUES ('do-not-publish')"
            )

    (root / "VERSION").write_text("1.7.3\n", encoding="utf-8")
    (root / ".env").write_text("PRIVATE_TOKEN=field-secret\n", encoding="utf-8")
    return database


def _capture(tmp_path: Path, *, with_secret: bool = False):
    source = tmp_path / "legacy"
    source.mkdir()
    _legacy_installation(source, with_secret=with_secret)
    return capture_legacy_installation(
        source,
        tmp_path / "captures",
        now=datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc),
    )


def test_preserve_creates_capture_compatible_sanitized_bundle(tmp_path):
    capture = _capture(tmp_path)
    source_before = capture.database_path.read_bytes()

    preserved = preserve_capture(
        capture.path,
        tmp_path / "preserved",
        now=datetime(2026, 9, 25, 17, 5, tzinfo=timezone.utc),
    )

    assert capture.database_path.read_bytes() == source_before
    assert verify_capture(preserved.path)["verified"] is True

    manifest = json.loads(preserved.manifest_path.read_text(encoding="utf-8"))
    assert manifest["format"] == "arthexis-legacy-capture-v1"
    assert manifest["preservation"]["format"] == PRESERVATION_FORMAT
    assert manifest["preservation"]["source_capture_id"] == capture.capture_id
    assert manifest["source"]["paths_exported"] is False
    assert manifest["source"]["configuration_inventory_exported"] is False
    assert manifest["metadata_files"] == []

    rendered_manifest = preserved.manifest_path.read_text(encoding="utf-8")
    assert str(capture.path) not in rendered_manifest
    assert "field-secret" not in rendered_manifest


def test_preserve_pseudonymizes_related_identifiers_consistently(tmp_path):
    capture = _capture(tmp_path)
    preserved = preserve_capture(capture.path, tmp_path / "preserved")

    with sqlite3.connect(preserved.database_path) as connection:
        card = connection.execute("SELECT rfid, ocpp_id_tag FROM core_rfid").fetchone()
        attempt = connection.execute(
            "SELECT rfid FROM cards_rfidattempt"
        ).fetchone()
        account = connection.execute(
            "SELECT name, ocpp_id_tag FROM core_account"
        ).fetchone()
        charger = connection.execute(
            "SELECT charger_id FROM ocpp_charger"
        ).fetchone()
        transaction = connection.execute(
            "SELECT id_tag FROM ocpp_transaction"
        ).fetchone()

    assert card[0] == attempt[0]
    assert card[1] == account[1] == transaction[0]
    assert card[0] != "REAL-CARD-123"
    assert card[1] != "REAL-TAG-123"
    assert account[0] != "Alice Example"
    assert charger[0] != "FIELD-CHARGER-01"


def test_preserve_is_deterministic_for_same_source_values(tmp_path):
    capture = _capture(tmp_path)

    first = preserve_capture(
        capture.path,
        tmp_path / "preserved-a",
        now=datetime(2026, 9, 25, 17, 10, tzinfo=timezone.utc),
    )
    second = preserve_capture(
        capture.path,
        tmp_path / "preserved-b",
        now=datetime(2026, 9, 25, 17, 11, tzinfo=timezone.utc),
    )

    with sqlite3.connect(first.database_path) as connection:
        first_tag = connection.execute(
            "SELECT ocpp_id_tag FROM core_rfid"
        ).fetchone()[0]
    with sqlite3.connect(second.database_path) as connection:
        second_tag = connection.execute(
            "SELECT ocpp_id_tag FROM core_rfid"
        ).fetchone()[0]

    assert first_tag == second_tag


def test_preserve_fails_closed_on_unhandled_secret_column(tmp_path):
    capture = _capture(tmp_path, with_secret=True)

    with pytest.raises(ValueError, match="secret-bearing column"):
        preserve_capture(capture.path, tmp_path / "preserved")

    assert not list((tmp_path / "preserved").glob("preserved-*"))

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from arthexis.reconciliation.source import classify_database, inspect_source, resolve_source


class ReconciliationSourceTests(SimpleTestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "db.sqlite3"
        with sqlite3.connect(self.database) as connection:
            connection.executescript(
                """
                CREATE TABLE django_migrations (
                    id integer primary key,
                    app text,
                    name text,
                    applied text
                );
                CREATE TABLE ocpp_charger (
                    id integer primary key,
                    charger_id text
                );
                INSERT INTO ocpp_charger VALUES (1, 'charger-1');
                """
            )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_installation_directory_resolves_known_database(self) -> None:
        self.assertEqual(resolve_source(self.root), self.database.resolve())

    def test_explicit_database_bypasses_installation_discovery(self) -> None:
        alternate = self.root / "snapshot.sqlite3"
        alternate.write_bytes(self.database.read_bytes())

        self.assertEqual(
            resolve_source(self.root, database=alternate),
            alternate.resolve(),
        )

    def test_ambiguous_installation_requires_explicit_database(self) -> None:
        var = self.root / "var"
        var.mkdir()
        (var / "db.sqlite3").write_bytes(self.database.read_bytes())

        with self.assertRaisesMessage(ValueError, "multiple database candidates"):
            resolve_source(self.root)

    def test_inspection_is_read_only_and_reports_integrity_fingerprint_and_counts(
        self,
    ) -> None:
        before = self.database.read_bytes()

        inspection = inspect_source(self.database)

        self.assertEqual(inspection.classification, "legacy")
        self.assertEqual(inspection.integrity, "ok")
        self.assertEqual(inspection.size, len(before))
        self.assertEqual(inspection.sha256, hashlib.sha256(before).hexdigest())
        self.assertEqual(inspection.tables["ocpp_charger"], 1)
        self.assertEqual(self.database.read_bytes(), before)

    def test_corrupt_sqlite_is_rejected(self) -> None:
        corrupt = self.root / "corrupt.sqlite3"
        corrupt.write_bytes(b"not a sqlite database")

        with self.assertRaisesMessage(ValueError, "not readable as SQLite"):
            inspect_source(corrupt)


class LegacyDatabaseGuardTests(SimpleTestCase):
    def test_missing_database_is_fresh(self) -> None:
        with TemporaryDirectory() as directory:
            self.assertEqual(
                classify_database(Path(directory) / "db.sqlite3"),
                "fresh",
            )

    def test_django_database_without_v2_marker_is_legacy(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "db.sqlite3"
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    "CREATE TABLE django_migrations (id integer primary key)"
                )

            self.assertEqual(classify_database(database_path), "legacy")

    def test_database_with_v2_marker_is_accepted(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "db.sqlite3"
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    "CREATE TABLE base_schemageneration (generation integer not null)"
                )
                connection.execute("INSERT INTO base_schemageneration VALUES (2)")

            self.assertEqual(classify_database(database_path), "v2")

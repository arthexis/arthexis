import sqlite3
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from scripts.detect_legacy_database import classify_database


class LegacyDatabaseGuardTests(SimpleTestCase):
    def test_missing_database_is_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            assert classify_database(Path(directory) / "db.sqlite3") == "fresh"

    def test_django_database_without_v2_marker_is_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "db.sqlite3"
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    "CREATE TABLE django_migrations (id integer primary key)"
                )

            assert classify_database(database_path) == "legacy"

    def test_database_with_v2_marker_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "db.sqlite3"
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    "CREATE TABLE base_schemageneration (generation integer not null)"
                )
                connection.execute("INSERT INTO base_schemageneration VALUES (2)")

            assert classify_database(database_path) == "v2"

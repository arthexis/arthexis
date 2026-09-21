"""Read-only SQLite access and preflight inspection for legacy sources."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


KNOWN_DATABASE_PATHS = (
    Path("db.sqlite3"),
    Path("var/db.sqlite3"),
)

RETAINED_LEGACY_TABLES = {
    "core_rfid",
    "core_energytariff",
    "core_account",
    "core_sigilroot",
    "core_stationmodel",
    "ocpp_charger",
}


@dataclass(frozen=True)
class SourceInspection:
    """Read-only facts about one legacy SQLite source."""

    path: Path
    classification: str
    integrity: str
    sha256: str
    size: int
    tables: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "classification": self.classification,
            "integrity": self.integrity,
            "sha256": self.sha256,
            "size": self.size,
            "tables": self.tables,
        }


def resolve_source(source: Path | None = None, *, database: Path | None = None) -> Path:
    """Resolve one explicit DB path or a single known DB inside an installation."""
    if database is not None:
        path = database.expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Legacy database does not exist: {path}")
        return path

    if source is None:
        raise ValueError("Provide an Arthexis installation path or --database.")

    path = source.expanduser().resolve()
    if path.is_file():
        return path
    if not path.is_dir():
        raise ValueError(f"Legacy source does not exist: {path}")

    candidates = [path / relative for relative in KNOWN_DATABASE_PATHS]
    matches = [candidate for candidate in candidates if candidate.is_file()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        expected = ", ".join(str(relative) for relative in KNOWN_DATABASE_PATHS)
        raise ValueError(
            f"No legacy SQLite database found in {path}; expected one of: {expected}"
        )
    raise ValueError(
        "Legacy installation contains multiple database candidates; "
        "use --database to select one explicitly."
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify_database(database_path: Path) -> str:
    """Return fresh, v2, legacy, or unreadable for one SQLite database path."""
    if not database_path.exists() or database_path.stat().st_size == 0:
        return "fresh"

    try:
        with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if "base_schemageneration" in tables:
                generations = connection.execute(
                    "SELECT generation FROM base_schemageneration"
                ).fetchall()
                if any(generation == 2 for (generation,) in generations):
                    return "v2"
            if "django_migrations" in tables or tables.intersection(
                RETAINED_LEGACY_TABLES
            ):
                return "legacy"
    except sqlite3.DatabaseError:
        return "unreadable"

    return "fresh"


def inspect_source(database_path: Path) -> SourceInspection:
    """Inspect one source without modifying it and verify SQLite integrity."""
    path = database_path.expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Legacy database does not exist: {path}")

    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
            integrity = "\n".join(str(row[0]) for row in integrity_rows)
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY name"
                )
            }
            counts: dict[str, int] = {}
            for table in tables:
                quoted = table.replace('"', '""')
                counts[table] = connection.execute(
                    f'SELECT COUNT(*) FROM "{quoted}"'
                ).fetchone()[0]
    except sqlite3.DatabaseError as error:
        raise ValueError(f"Legacy database is not readable as SQLite: {path}") from error

    if integrity != "ok":
        raise ValueError(f"SQLite integrity check failed for {path}: {integrity}")

    return SourceInspection(
        path=path,
        classification=classify_database(path),
        integrity=integrity,
        sha256=_sha256(path),
        size=path.stat().st_size,
        tables=counts,
    )


class LegacySource:
    """Expose only schema-checked rows from one legacy SQLite database."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> LegacySource:
        if not self.path.is_file():
            raise ValueError(f"Legacy database does not exist: {self.path}")
        self.connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        self.connection.row_factory = sqlite3.Row
        return self

    def __exit__(self, *_: object) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    @property
    def tables(self) -> set[str]:
        assert self.connection is not None
        return {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    def validate(self) -> None:
        """Reject unrelated SQLite files before the destination transaction opens."""
        if not self.tables.intersection(RETAINED_LEGACY_TABLES):
            raise ValueError(
                "Legacy database has none of the supported Arthexis 1.x tables."
            )

    def rows(self, *candidates: str) -> tuple[str | None, list[dict[str, Any]]]:
        """Return rows from the first available candidate table."""
        assert self.connection is not None
        table = next((name for name in candidates if name in self.tables), None)
        if table is None:
            return None, []
        quoted = table.replace('"', '""')
        return table, [
            dict(row) for row in self.connection.execute(f'SELECT * FROM "{quoted}"')
        ]

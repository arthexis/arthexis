"""Read-only SQLite access for the legacy interchange."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


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
        retained_tables = {
            "core_rfid",
            "core_energytariff",
            "core_account",
            "core_sigilroot",
            "core_stationmodel",
            "ocpp_charger",
        }
        if not self.tables.intersection(retained_tables):
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

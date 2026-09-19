#!/usr/bin/env python3
"""Classify one explicitly selected SQLite database without modifying it."""

import argparse
import sqlite3
from pathlib import Path


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
            if "django_migrations" in tables:
                return "legacy"
    except sqlite3.DatabaseError:
        return "unreadable"

    return "fresh"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    arguments = parser.parse_args()
    status = classify_database(arguments.database)

    if status in {"fresh", "v2"}:
        print(f"Database is safe for Arthexis 2.0 install: {status}.")
        return 0
    if status == "legacy":
        print(
            "Existing Django database appears to be Arthexis 1.x or another "
            "legacy database. It was not changed. Arthexis 2.0 reconciliation "
            "is intentionally deferred until the 2.0 structure is complete."
        )
        return 2

    print("Database is not readable as SQLite. It was not changed.")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())

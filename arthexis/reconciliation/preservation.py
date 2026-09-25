"""Preserve repository-safe historical fixtures from finalized capture bundles."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from arthexis.reconciliation.capture import CAPTURE_FORMAT, verify_capture
from arthexis.reconciliation.source import inspect_source

PRESERVATION_FORMAT = "arthexis-preserved-capture-v1"

# Fields are pseudonymized by semantic namespace so repeated values keep their
# relationships across tables without retaining field/customer identifiers.
PSEUDONYM_FIELDS: dict[str, dict[str, str]] = {
    "core_rfid": {
        "rfid": "card",
        "uid": "card",
        "ocpp_id_tag": "id-tag",
        "custom_label": "card-label",
        "generated_label": "card-label",
    },
    "cards_rfidattempt": {
        "rfid": "card",
        "presented_id": "card",
    },
    "core_account": {
        "name": "account",
        "ocpp_id_tag": "id-tag",
    },
    "nodes_node": {
        "identifier": "node",
        "name": "node",
    },
    "core_node": {
        "identifier": "node",
        "name": "node",
    },
    "ocpp_charger": {
        "charger_id": "charger",
        "identity": "charger",
    },
    "ocpp_transaction": {
        "id_tag": "id-tag",
        "idTag": "id-tag",
    },
}

SECRET_COLUMN_MARKERS = (
    "password",
    "passwd",
    "secret",
    "private_key",
    "api_key",
    "access_token",
    "refresh_token",
    "connection_token",
)


@dataclass(frozen=True)
class PreservedCaptureResult:
    """Finalized repository-safe capture derived from a verified field capture."""

    capture_id: str
    path: Path
    database_path: Path
    manifest_path: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pseudonym(namespace: str, value: object) -> str:
    raw = str(value)
    digest = hashlib.sha256(f"{namespace}:{raw}".encode()).hexdigest()[:16]
    prefixes = {
        "card": "CARD",
        "id-tag": "TAG",
        "card-label": "Card",
        "account": "Account",
        "node": "Node",
        "charger": "CHARGER",
    }
    return f"{prefixes.get(namespace, namespace)}-{digest}"


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    quoted = table.replace('"', '""')
    return {
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{quoted}")')
    }


def _reject_unhandled_secrets(connection: sqlite3.Connection) -> None:
    tables = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for table in tables:
        quoted_table = table.replace('"', '""')
        for column in _columns(connection, table):
            lowered = column.lower()
            if not any(marker in lowered for marker in SECRET_COLUMN_MARKERS):
                continue
            quoted_column = column.replace('"', '""')
            present = connection.execute(
                f'SELECT 1 FROM "{quoted_table}" '
                f'WHERE "{quoted_column}" IS NOT NULL '
                f'AND CAST("{quoted_column}" AS TEXT) != "" LIMIT 1'
            ).fetchone()
            if present is not None:
                raise ValueError(
                    "Preservation found an unhandled secret-bearing column: "
                    f"{table}.{column}"
                )


def _sanitize_database(database: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with sqlite3.connect(database) as connection:
        _reject_unhandled_secrets(connection)
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for table, requested in PSEUDONYM_FIELDS.items():
            if table not in tables:
                continue
            available = _columns(connection, table)
            quoted_table = table.replace('"', '""')
            for column, namespace in requested.items():
                if column not in available:
                    continue
                quoted_column = column.replace('"', '""')
                rows = connection.execute(
                    f'SELECT rowid, "{quoted_column}" FROM "{quoted_table}" '
                    f'WHERE "{quoted_column}" IS NOT NULL '
                    f'AND CAST("{quoted_column}" AS TEXT) != ""'
                ).fetchall()
                for rowid, value in rows:
                    connection.execute(
                        f'UPDATE "{quoted_table}" SET "{quoted_column}" = ? '
                        "WHERE rowid = ?",
                        (_pseudonym(namespace, value), rowid),
                    )
                if rows:
                    counts[f"{table}.{column}"] = len(rows)

        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"Sanitized database failed integrity check: {integrity}")
        connection.commit()
    return counts


def _preserved_id(source_capture_id: str, database_sha256: str) -> str:
    return f"preserved-{source_capture_id}-{database_sha256[:12]}"


def preserve_capture(
    source_capture: Path,
    destination_root: Path,
    *,
    now: datetime | None = None,
) -> PreservedCaptureResult:
    """Create a sanitized finalized capture without modifying its private source."""

    source = source_capture.expanduser().resolve()
    verification = verify_capture(source)
    source_manifest = json.loads(
        (source / "manifest.json").read_text(encoding="utf-8")
    )
    source_database = source / source_manifest["database"]["path"]

    root = destination_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / f".preserve-{os.getpid()}"
    if temporary.exists():
        raise ValueError(f"Preservation staging path already exists: {temporary}")
    temporary.mkdir()

    try:
        database = temporary / "database" / "legacy.sqlite3"
        database.parent.mkdir()
        shutil.copy2(source_database, database)
        sanitized = _sanitize_database(database)
        inspection = inspect_source(database)
        if inspection.classification != "legacy":
            raise ValueError(
                "Sanitized database no longer classifies as legacy; "
                f"detected {inspection.classification}."
            )

        capture_id = _preserved_id(
            str(verification["capture_id"]), inspection.sha256
        )
        final_path = root / capture_id
        if final_path.exists():
            raise ValueError(f"Preserved capture already exists: {final_path}")

        captured_at = now or datetime.now(timezone.utc)
        manifest = {
            "format": CAPTURE_FORMAT,
            "capture_id": capture_id,
            "captured_at": captured_at.isoformat(),
            "source": {
                "preserved_from_capture_id": verification["capture_id"],
                "legacy_version": source_manifest.get("source", {}).get("version"),
                "legacy_revision_retained": bool(
                    source_manifest.get("source", {}).get("revision")
                ),
                "paths_exported": False,
                "configuration_inventory_exported": False,
            },
            "database": {
                "path": "database/legacy.sqlite3",
                "classification": inspection.classification,
                "integrity": inspection.integrity,
                "size": inspection.size,
                "sha256": inspection.sha256,
                "tables": inspection.tables,
                "snapshot_method": "sanitized-preserved-capture",
            },
            "metadata_files": [],
            "warnings": [
                "This is a sanitized historical fixture, not the authoritative field capture."
            ],
            "preservation": {
                "format": PRESERVATION_FORMAT,
                "source_capture_id": verification["capture_id"],
                "source_database_sha256": verification["database_sha256"],
                "sanitized_fields": sanitized,
                "source_paths_retained": False,
                "configuration_fingerprints_retained": False,
            },
        }
        manifest_path = temporary / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        checksum_entries = [
            ("database/legacy.sqlite3", _sha256(database)),
            ("manifest.json", _sha256(manifest_path)),
        ]
        (temporary / "checksums.sha256").write_text(
            "".join(f"{digest}  {path}\n" for path, digest in checksum_entries),
            encoding="utf-8",
        )
        (temporary / "FINALIZED").write_text(
            json.dumps(
                {
                    "format": CAPTURE_FORMAT,
                    "capture_id": capture_id,
                    "manifest_sha256": _sha256(manifest_path),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.rename(final_path)

        # Reuse the capture verifier as the compatibility contract for the output.
        verify_capture(final_path)
        return PreservedCaptureResult(
            capture_id=capture_id,
            path=final_path,
            database_path=final_path / "database" / "legacy.sqlite3",
            manifest_path=final_path / "manifest.json",
        )
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

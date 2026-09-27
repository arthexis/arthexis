"""Resolve replay inputs from databases and finalized capture packages."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from arthexis.reconciliation.capture import CAPTURE_FORMAT, verify_capture
from arthexis.reconciliation.source import inspect_source


@dataclass(frozen=True)
class ReplayDatabaseSource:
    """Resolved read-only database source and provenance."""

    database: Path
    kind: str
    capture_id: str | None = None


def resolve_replay_database(source: Path) -> ReplayDatabaseSource:
    """Resolve a bare v2 DB or a finalized capture/package containing one."""
    path = source.expanduser().resolve()
    if path.is_file():
        _require_replayable(path)
        return ReplayDatabaseSource(database=path, kind="database")

    if not path.is_dir():
        raise ValueError(f"Replay source does not exist: {path}")

    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(
            "Replay package must contain manifest.json identifying its database."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    database = manifest.get("database")
    if not isinstance(database, dict) or not isinstance(database.get("path"), str):
        raise ValueError("Replay package manifest does not identify a database path.")

    if manifest.get("format") == CAPTURE_FORMAT:
        verification = verify_capture(path)
        candidate = (path / database["path"]).resolve()
        _require_inside(path, candidate)
        inspection = inspect_source(candidate)
        if inspection.classification != "v2":
            raise ValueError(
                "Capture package contains a legacy database; reconcile the capture "
                "to a current-generation Arthexis database before OCPP replay."
            )
        return ReplayDatabaseSource(
            database=candidate,
            kind="capture",
            capture_id=str(verification["capture_id"]),
        )

    candidate = (path / database["path"]).resolve()
    _require_inside(path, candidate)
    _require_replayable(candidate)
    return ReplayDatabaseSource(
        database=candidate,
        kind="package",
        capture_id=_optional_text(manifest.get("capture_id")),
    )


def _require_replayable(database: Path) -> None:
    inspection = inspect_source(database)
    if inspection.classification != "v2":
        raise ValueError(
            "OCPP replay requires a migrated/current-generation Arthexis database; "
            f"detected {inspection.classification}."
        )


def _require_inside(root: Path, candidate: Path) -> None:
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError("Replay package database path escapes the package.") from error


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None

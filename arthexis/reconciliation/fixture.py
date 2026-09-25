"""Create disposable database-only fixtures from verified legacy captures."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from arthexis.reconciliation.capture import verify_capture
from arthexis.reconciliation.source import inspect_source

FIXTURE_FORMAT = "arthexis-migration-fixture-v1"


@dataclass(frozen=True)
class FixtureResult:
    """Disposable database working copy derived from one verified capture."""

    fixture_id: str
    path: Path
    database_path: Path
    metadata_path: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fixture_id(now: datetime, capture_id: str, database_sha256: str) -> str:
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{capture_id[-12:]}-{database_sha256[:8]}"


def restore_fixture(
    capture_path: Path,
    destination_root: Path,
    *,
    now: datetime | None = None,
) -> FixtureResult:
    """Verify a capture, then copy only its SQLite DB into a new disposable fixture."""

    capture = capture_path.expanduser().resolve()
    verification = verify_capture(capture)
    manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
    source_database = capture / manifest["database"]["path"]

    created_at = now or datetime.now(timezone.utc)
    destination = destination_root.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)

    fixture_id = _fixture_id(
        created_at,
        str(verification["capture_id"]),
        str(verification["database_sha256"]),
    )
    final_path = destination / fixture_id
    if final_path.exists():
        raise ValueError(f"Fixture already exists: {final_path}")

    temporary = destination / f".fixture-{os.getpid()}"
    if temporary.exists():
        raise ValueError(f"Fixture staging path already exists: {temporary}")
    temporary.mkdir()

    try:
        database_path = temporary / "database.sqlite3"
        shutil.copyfile(source_database, database_path)

        inspection = inspect_source(database_path)
        copied_sha = _sha256(database_path)
        if copied_sha != verification["database_sha256"]:
            raise ValueError("Restored fixture database does not match source capture.")
        if inspection.classification != "legacy":
            raise ValueError(
                "Restored fixture database is not classified as legacy; "
                f"detected {inspection.classification}."
            )

        metadata = {
            "format": FIXTURE_FORMAT,
            "fixture_id": fixture_id,
            "created_at": created_at.isoformat(),
            "source_capture_id": verification["capture_id"],
            "source_manifest_sha256": verification["manifest_sha256"],
            "source_database_sha256": verification["database_sha256"],
            "database": {
                "path": "database.sqlite3",
                "sha256_at_creation": copied_sha,
                "classification_at_creation": inspection.classification,
                "integrity_at_creation": inspection.integrity,
            },
        }
        metadata_path = temporary / "fixture.json"
        metadata_path.write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        temporary.rename(final_path)
        return FixtureResult(
            fixture_id=fixture_id,
            path=final_path,
            database_path=final_path / "database.sqlite3",
            metadata_path=final_path / "fixture.json",
        )
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

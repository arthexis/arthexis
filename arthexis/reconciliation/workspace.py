"""Reconcile a verified fixture into a fresh Arthexis 2 database."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

from django.conf import settings

from arthexis.reconciliation.fixture import FIXTURE_FORMAT
from arthexis.reconciliation.importer import ReconciliationReport, reconcile
from arthexis.reconciliation.source import inspect_source

RECONCILIATION_WORKSPACE_FORMAT = "arthexis-migration-reconciliation-v1"


def _peak_memory_usage() -> dict[str, int]:
    """Return peak RSS on Linux when the standard resource module is available."""

    if not sys.platform.startswith("linux"):
        return {}
    try:
        import resource
    except ImportError:
        return {}
    return {"peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_fixture_source(path: Path) -> tuple[dict[str, object], Path]:
    """Verify the disposable legacy source still matches its creation evidence."""

    fixture = path.expanduser().resolve()
    metadata_path = fixture / "fixture.json"
    database_path = fixture / "database.sqlite3"
    if not metadata_path.is_file() or not database_path.is_file():
        raise ValueError(f"Fixture is incomplete: {fixture}")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("format") != FIXTURE_FORMAT:
        raise ValueError(f"Unsupported fixture format: {fixture}")

    expected = metadata["database"]["sha256_at_creation"]
    actual = _sha256(database_path)
    if actual != expected:
        raise ValueError("Fixture source database changed after restore.")

    inspection = inspect_source(database_path)
    if inspection.classification != "legacy":
        raise ValueError(
            "Fixture source must remain a legacy database; "
            f"detected {inspection.classification}."
        )
    return metadata, database_path


def reconcile_fixture(
    fixture_path: Path,
    destination_database: Path,
    *,
    batch_size: int = 250,
) -> tuple[ReconciliationReport, Path]:
    """Import a verified fixture into the configured fresh 2.0 destination."""

    fixture = fixture_path.expanduser().resolve()
    destination = destination_database.expanduser().resolve()
    metadata, source_database = verify_fixture_source(fixture)
    source_sha_before = _sha256(source_database)

    configured = Path(settings.DATABASES["default"]["NAME"]).expanduser().resolve()
    if configured != destination:
        raise ValueError(
            "Configured Arthexis destination database does not match requested output."
        )

    started = time.monotonic()
    report = reconcile(source_database, batch_size=batch_size)
    elapsed_seconds = round(time.monotonic() - started, 3)
    source_sha_after = _sha256(source_database)
    if source_sha_after != source_sha_before:
        raise RuntimeError("Reconciliation modified the fixture source database.")

    output_inspection = inspect_source(destination)
    if output_inspection.classification != "v2":
        raise RuntimeError(
            "Reconciliation destination is not an Arthexis 2 database; "
            f"detected {output_inspection.classification}."
        )

    receipt = {
        "format": RECONCILIATION_WORKSPACE_FORMAT,
        "status": "success",
        "fixture_id": metadata["fixture_id"],
        "source_capture_id": metadata["source_capture_id"],
        "source_database_sha256": source_sha_before,
        "destination_database": {
            "path": destination.name,
            "sha256": output_inspection.sha256,
            "size": output_inspection.size,
            "classification": output_inspection.classification,
            "integrity": output_inspection.integrity,
        },
        "reconciliation": report.as_dict(),
        "resource_policy": {
            "batch_size": batch_size,
        },
        "resource_usage": {
            "elapsed_seconds": elapsed_seconds,
            **_peak_memory_usage(),
        },
    }
    receipt_path = fixture / "reconciliation.json"
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report, receipt_path


def write_failure_receipt(fixture_path: Path, error: Exception) -> Path | None:
    """Leave concise diagnostics when fixture reconciliation fails."""

    fixture = fixture_path.expanduser().resolve()
    metadata_path = fixture / "fixture.json"
    if not fixture.is_dir() or not metadata_path.is_file():
        return None

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    receipt = {
        "format": RECONCILIATION_WORKSPACE_FORMAT,
        "status": "failed",
        "fixture_id": metadata.get("fixture_id"),
        "source_capture_id": metadata.get("source_capture_id"),
        "error": {
            "type": type(error).__name__,
            "message": str(error),
        },
    }
    receipt_path = fixture / "reconciliation-error.json"
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt_path

"""Repository corpus of accepted sanitized legacy capture fixtures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from arthexis.reconciliation.capture import verify_capture
from arthexis.reconciliation.preservation import PRESERVATION_FORMAT

DEFAULT_CORPUS = Path("tests/fixtures/reconciliation")


@dataclass(frozen=True)
class PreservedFixture:
    """One accepted repository fixture and its preservation metadata."""

    name: str
    path: Path
    source_version: str | None
    source_capture_id: str
    database_sha256: str


def discover_preserved_fixtures(root: Path = DEFAULT_CORPUS) -> tuple[Path, ...]:
    """Return finalized fixture bundles in deterministic repository order."""

    corpus = root.expanduser().resolve()
    if not corpus.exists():
        return ()
    return tuple(
        path
        for path in sorted(corpus.iterdir())
        if path.is_dir() and (path / "FINALIZED").is_file()
    )


def inspect_preserved_fixture(path: Path) -> PreservedFixture:
    """Verify one accepted fixture and require preservation provenance."""

    capture = path.expanduser().resolve()
    verification = verify_capture(capture)
    manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
    preservation = manifest.get("preservation")
    if not isinstance(preservation, dict):
        raise ValueError(f"Accepted fixture lacks preservation metadata: {capture}")
    if preservation.get("format") != PRESERVATION_FORMAT:
        raise ValueError(f"Unsupported preserved fixture format: {capture}")
    source_capture_id = preservation.get("source_capture_id")
    if not isinstance(source_capture_id, str) or not source_capture_id:
        raise ValueError(f"Accepted fixture lacks source capture provenance: {capture}")
    if manifest.get("metadata_files"):
        raise ValueError(f"Accepted fixture exports legacy metadata files: {capture}")
    source = manifest.get("source", {})
    if not isinstance(source, dict):
        raise ValueError(f"Accepted fixture has invalid source metadata: {capture}")
    if source.get("paths_exported") is not False:
        raise ValueError(f"Accepted fixture may expose source paths: {capture}")
    if source.get("configuration_inventory_exported") is not False:
        raise ValueError(
            f"Accepted fixture may expose configuration fingerprints: {capture}"
        )

    return PreservedFixture(
        name=capture.name,
        path=capture,
        source_version=source.get("legacy_version"),
        source_capture_id=source_capture_id,
        database_sha256=str(verification["database_sha256"]),
    )


def inspect_preserved_corpus(
    root: Path = DEFAULT_CORPUS,
) -> tuple[PreservedFixture, ...]:
    """Verify every accepted fixture in the repository corpus."""

    return tuple(inspect_preserved_fixture(path) for path in discover_preserved_fixtures(root))

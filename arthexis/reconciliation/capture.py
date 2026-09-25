"""Create verified point-in-time captures from passive legacy installations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from arthexis.reconciliation.source import inspect_source, resolve_source

CAPTURE_FORMAT = "arthexis-legacy-capture-v1"
SAFE_METADATA_FILES = (
    Path("VERSION"),
    Path("pyproject.toml"),
    Path("requirements.txt"),
    Path("requirements/base.txt"),
    Path("requirements/prod.txt"),
    Path("requirements/production.txt"),
)
CONFIGURATION_GLOBS = (
    ".env",
    "*.env",
    "gway.toml",
    "config/*.toml",
    "config/*.json",
    "config/*.yaml",
    "config/*.yml",
    "config/*.ini",
    "config/*.cfg",
)


@dataclass(frozen=True)
class LegacyInstallation:
    """Discovered passive source installation."""

    root: Path
    database: Path
    version: str | None
    revision: str | None
    configuration_inventory: tuple[dict[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "database": str(self.database),
            "version": self.version,
            "revision": self.revision,
            "configuration_inventory": list(self.configuration_inventory),
        }


@dataclass(frozen=True)
class CaptureResult:
    """Finalized capture bundle."""

    capture_id: str
    path: Path
    manifest_path: Path
    database_path: Path
    manifest_sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_text(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    return value or None


def _git_revision(root: Path) -> str | None:
    head = _read_text(root / ".git" / "HEAD")
    if not head:
        return None
    if not head.startswith("ref: "):
        return head
    ref = root / ".git" / head[5:].strip()
    return _read_text(ref)


def _configuration_paths(root: Path) -> Iterable[Path]:
    seen: set[Path] = set()
    for pattern in CONFIGURATION_GLOBS:
        for path in root.glob(pattern):
            if path.is_file():
                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    yield path


def _fingerprint_configuration(root: Path) -> tuple[dict[str, object], ...]:
    items: list[dict[str, object]] = []
    for path in sorted(_configuration_paths(root)):
        items.append(
            {
                "path": str(path.relative_to(root)),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
                "content_exported": False,
            }
        )
    return tuple(items)


def discover_legacy_installation(
    source: Path, *, database: Path | None = None
) -> LegacyInstallation:
    """Discover one legacy installation without executing code inside it."""

    source_path = source.expanduser().resolve()
    database_path = resolve_source(source_path, database=database)
    root = source_path if source_path.is_dir() else database_path.parent
    inspection = inspect_source(database_path)
    if inspection.classification != "legacy":
        raise ValueError(
            "Capture requires a legacy Arthexis database; "
            f"detected {inspection.classification}."
        )

    return LegacyInstallation(
        root=root,
        database=database_path,
        version=_read_text(root / "VERSION"),
        revision=_git_revision(root),
        configuration_inventory=_fingerprint_configuration(root),
    )


def _snapshot_sqlite(source: Path, destination: Path) -> None:
    """Take a consistent SQLite snapshot while the legacy process may be live."""

    destination.parent.mkdir(parents=True, exist_ok=False)
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as origin:
            with sqlite3.connect(destination) as target:
                origin.backup(target)
    except sqlite3.DatabaseError as error:
        raise ValueError(f"Could not capture legacy SQLite database: {source}") from error


def _copy_safe_metadata(
    installation: LegacyInstallation, destination: Path
) -> list[dict[str, object]]:
    copied: list[dict[str, object]] = []
    for relative in SAFE_METADATA_FILES:
        source = installation.root / relative
        if not source.is_file():
            continue
        target = destination / "metadata" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(
            {
                "path": str(target.relative_to(destination)),
                "source_path": str(relative),
                "size": target.stat().st_size,
                "sha256": _sha256(target),
            }
        )
    return copied


def _capture_id(now: datetime, database_sha256: str) -> str:
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{database_sha256[:12]}"


def capture_legacy_installation(
    source: Path,
    destination_root: Path,
    *,
    database: Path | None = None,
    now: datetime | None = None,
) -> CaptureResult:
    """Capture a passive legacy installation into a finalized immutable input bundle."""

    installation = discover_legacy_installation(source, database=database)
    captured_at = now or datetime.now(timezone.utc)

    staging_root = destination_root.expanduser().resolve()
    staging_root.mkdir(parents=True, exist_ok=True)

    temporary = staging_root / f".capture-{os.getpid()}"
    if temporary.exists():
        raise ValueError(f"Capture staging path already exists: {temporary}")
    temporary.mkdir()

    try:
        snapshot = temporary / "database" / "legacy.sqlite3"
        _snapshot_sqlite(installation.database, snapshot)
        snapshot_inspection = inspect_source(snapshot)
        if snapshot_inspection.classification != "legacy":
            raise ValueError(
                "Captured database is not classified as legacy; "
                f"detected {snapshot_inspection.classification}."
            )

        capture_id = _capture_id(captured_at, snapshot_inspection.sha256)
        final_path = staging_root / capture_id
        if final_path.exists():
            raise ValueError(f"Capture already exists: {final_path}")

        copied_metadata = _copy_safe_metadata(installation, temporary)
        warnings: list[str] = []
        if installation.version is None:
            warnings.append("Legacy VERSION could not be detected.")
        if installation.revision is None:
            warnings.append("Legacy Git revision could not be detected.")
        if installation.configuration_inventory:
            warnings.append(
                "Configuration files were fingerprinted but their contents were not "
                "exported because they may contain secrets."
            )

        manifest = {
            "format": CAPTURE_FORMAT,
            "capture_id": capture_id,
            "captured_at": captured_at.isoformat(),
            "source": installation.as_dict(),
            "database": {
                "path": "database/legacy.sqlite3",
                "classification": snapshot_inspection.classification,
                "integrity": snapshot_inspection.integrity,
                "size": snapshot_inspection.size,
                "sha256": snapshot_inspection.sha256,
                "tables": snapshot_inspection.tables,
                "snapshot_method": "sqlite-online-backup",
            },
            "metadata_files": copied_metadata,
            "warnings": warnings,
        }
        manifest_path = temporary / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        checksum_entries = [
            ("database/legacy.sqlite3", _sha256(snapshot)),
            *[(item["path"], item["sha256"]) for item in copied_metadata],
            ("manifest.json", _sha256(manifest_path)),
        ]
        (temporary / "checksums.sha256").write_text(
            "".join(f"{digest}  {path}\n" for path, digest in checksum_entries),
            encoding="utf-8",
        )
        manifest_sha256 = _sha256(manifest_path)
        (temporary / "FINALIZED").write_text(
            json.dumps(
                {
                    "format": CAPTURE_FORMAT,
                    "capture_id": capture_id,
                    "manifest_sha256": manifest_sha256,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        temporary.rename(final_path)
        return CaptureResult(
            capture_id=capture_id,
            path=final_path,
            manifest_path=final_path / "manifest.json",
            database_path=final_path / "database" / "legacy.sqlite3",
            manifest_sha256=manifest_sha256,
        )
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def verify_capture(path: Path) -> dict[str, object]:
    """Verify a finalized capture before any downstream operation consumes it."""

    capture = path.expanduser().resolve()
    finalized = capture / "FINALIZED"
    manifest_path = capture / "manifest.json"
    checksums_path = capture / "checksums.sha256"
    if not finalized.is_file() or not manifest_path.is_file() or not checksums_path.is_file():
        raise ValueError(f"Capture is incomplete: {capture}")

    marker = json.loads(finalized.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if marker.get("format") != CAPTURE_FORMAT or manifest.get("format") != CAPTURE_FORMAT:
        raise ValueError(f"Unsupported capture format: {capture}")
    actual_manifest_sha = _sha256(manifest_path)
    if marker.get("manifest_sha256") != actual_manifest_sha:
        raise ValueError("Capture manifest checksum does not match FINALIZED marker.")

    for line in checksums_path.read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        target = capture / relative
        if not target.is_file() or _sha256(target) != digest:
            raise ValueError(f"Capture checksum failed: {relative}")

    database_path = capture / manifest["database"]["path"]
    inspection = inspect_source(database_path)
    if inspection.sha256 != manifest["database"]["sha256"]:
        raise ValueError("Captured database checksum does not match manifest.")

    return {
        "capture_id": manifest["capture_id"],
        "path": str(capture),
        "manifest_sha256": actual_manifest_sha,
        "database_sha256": inspection.sha256,
        "verified": True,
        "warnings": manifest.get("warnings", []),
    }

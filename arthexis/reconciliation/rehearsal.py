"""Resource safety policy for legacy migration rehearsals."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_MAX_ELAPSED_SECONDS = 1800.0
DEFAULT_MAX_PEAK_RSS_MIB = 512.0
DEFAULT_MAX_WORKSPACE_MIB = 2048.0
DEFAULT_MIN_FREE_DISK_MIB = 1024.0


@dataclass(frozen=True)
class ResourcePolicy:
    max_elapsed_seconds: float = DEFAULT_MAX_ELAPSED_SECONDS
    max_peak_rss_mib: float = DEFAULT_MAX_PEAK_RSS_MIB
    max_workspace_mib: float = DEFAULT_MAX_WORKSPACE_MIB
    min_free_disk_mib: float = DEFAULT_MIN_FREE_DISK_MIB

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def _mib(value: int) -> float:
    return round(value / (1024 * 1024), 3)


def workspace_size_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def preflight_disk(path: Path, policy: ResourcePolicy) -> dict[str, object]:
    """Require enough free disk before taking a live-source snapshot."""

    path.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(path)
    free_mib = _mib(usage.free)
    violations = []
    if free_mib < policy.min_free_disk_mib:
        violations.append(
            {
                "resource": "free_disk_mib",
                "observed": free_mib,
                "limit": policy.min_free_disk_mib,
                "comparison": "minimum",
            }
        )
    return {
        "free_disk_mib": free_mib,
        "violations": violations,
    }


def evaluate_resources(
    rehearsal_root: Path,
    reconciliation_receipt: Path,
    *,
    started: float,
    policy: ResourcePolicy,
) -> dict[str, object]:
    """Evaluate completed reconciliation evidence against hard safety limits."""

    receipt = json.loads(reconciliation_receipt.read_text(encoding="utf-8"))
    reconciliation_usage = receipt.get("resource_usage", {})
    total_elapsed = round(time.monotonic() - started, 3)
    peak_rss_kib = reconciliation_usage.get("peak_rss_kib")
    peak_rss_mib = (
        round(float(peak_rss_kib) / 1024, 3) if peak_rss_kib is not None else None
    )
    workspace_mib = _mib(workspace_size_bytes(rehearsal_root))

    violations: list[dict[str, object]] = []

    def maximum(resource: str, observed: float | None, limit: float) -> None:
        if observed is not None and observed > limit:
            violations.append(
                {
                    "resource": resource,
                    "observed": observed,
                    "limit": limit,
                    "comparison": "maximum",
                }
            )

    maximum("elapsed_seconds", total_elapsed, policy.max_elapsed_seconds)
    maximum("peak_rss_mib", peak_rss_mib, policy.max_peak_rss_mib)
    maximum("workspace_mib", workspace_mib, policy.max_workspace_mib)

    return {
        "policy": policy.as_dict(),
        "usage": {
            "elapsed_seconds": total_elapsed,
            "reconciliation_elapsed_seconds": reconciliation_usage.get(
                "elapsed_seconds"
            ),
            "peak_rss_mib": peak_rss_mib,
            "workspace_mib": workspace_mib,
        },
        "violations": violations,
        "decision": "NO-GO" if violations else "GO",
    }


def write_resource_receipt(
    rehearsal_root: Path,
    result: dict[str, object],
) -> Path:
    path = rehearsal_root / "resource-report.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


GO_BUNDLE_FORMAT = "arthexis-migration-go-bundle-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_go_bundle(
    rehearsal_root: Path,
    *,
    capture_path: Path,
    fixture_path: Path,
    migration_report: Path,
    migration_text_report: Path,
    resource_report: Path,
    cutover_report: Path | None = None,
) -> Path:
    """Create one immutable, checksummed handoff bundle for an accepted GO."""

    capture_manifest_path = capture_path / "manifest.json"
    reconciliation_path = fixture_path / "reconciliation.json"
    destination_database = fixture_path / "reconciled.sqlite3"

    required = [
        capture_manifest_path,
        reconciliation_path,
        destination_database,
        migration_report,
        migration_text_report,
        resource_report,
    ]
    if cutover_report is not None:
        required.append(cutover_report)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError(f"GO bundle inputs are incomplete: {', '.join(missing)}")

    capture_manifest = json.loads(capture_manifest_path.read_text(encoding="utf-8"))
    verification = json.loads(migration_report.read_text(encoding="utf-8"))
    resources = json.loads(resource_report.read_text(encoding="utf-8"))
    if verification.get("decision") != "GO":
        raise ValueError("GO bundle requires a successful migration verification.")
    if resources.get("decision") != "GO":
        raise ValueError("GO bundle requires resource safety decision GO.")

    destination_sha256 = _sha256(destination_database)
    capture_id = str(capture_manifest["capture_id"])
    bundle_id = f"{capture_id}-{destination_sha256[:12]}"
    bundles_root = rehearsal_root / "bundles"
    bundles_root.mkdir(parents=True, exist_ok=True)
    final_path = bundles_root / bundle_id
    if final_path.exists():
        raise ValueError(f"GO bundle already exists: {final_path}")

    temporary = bundles_root / f".bundle-{os.getpid()}"
    if temporary.exists():
        raise ValueError(f"GO bundle staging path already exists: {temporary}")
    temporary.mkdir()

    try:
        payloads = {
            "capture/manifest.json": capture_manifest_path,
            "capture/checksums.sha256": capture_path / "checksums.sha256",
            "migration/reconciliation.json": reconciliation_path,
            "migration/migration-report.json": migration_report,
            "migration/migration-report.txt": migration_text_report,
            "migration/resource-report.json": resource_report,
            "database/reconciled.sqlite3": destination_database,
        }
        if cutover_report is not None:
            payloads["migration/cutover-report.json"] = cutover_report
        checksums: list[tuple[str, str]] = []
        for relative, source in payloads.items():
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            checksums.append((relative, _sha256(target)))

        if cutover is not None:
            cutover_path = Path(str(cutover["proof_path"]))
            if not cutover_path.is_file():
                raise ValueError("Cutover GO bundle requires cutover proof evidence.")
            if cutover.get("decision") != "GO" or not cutover.get("no_missed_writes"):
                raise ValueError("Cutover GO bundle requires no-missed-writes proof.")
            target = temporary / "migration" / "cutover-proof.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cutover_path, target)
            checksums.append(("migration/cutover-proof.json", _sha256(target)))

        manifest = {
            "format": GO_BUNDLE_FORMAT,
            "bundle_id": bundle_id,
            "decision": "GO",
            "source": {
                "capture_id": capture_id,
                "capture_manifest_sha256": _sha256(capture_manifest_path),
                "database_sha256": capture_manifest["database"]["sha256"],
                "installation": capture_manifest.get("source", {}),
            },
            "destination": {
                "database_path": "database/reconciled.sqlite3",
                "database_sha256": destination_sha256,
                "classification": "v2",
            },
            "verification": {
                "report_path": "migration/migration-report.json",
                "historical_gaps": verification.get("historical_gaps", []),
                "warnings": verification.get("warnings", []),
            },
            "resources": {
                "report_path": "migration/resource-report.json",
                "policy": resources.get("policy", {}),
                "usage": resources.get("usage", {}),
            },
            "cutover": (
                {
                    "report_path": "migration/cutover-report.json",
                    "mode": "final-cutover",
                    "no_missed_writes_proven": True,
                }
                if cutover_report is not None
                else {"mode": "ordinary-rehearsal"}
            ),
            "provenance": {
                "reconciliation_receipt": "migration/reconciliation.json",
                "capture_manifest": "capture/manifest.json",
            },
        }
        manifest_path = temporary / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        checksums.append(("manifest.json", _sha256(manifest_path)))
        (temporary / "checksums.sha256").write_text(
            "".join(f"{digest}  {relative}\n" for relative, digest in checksums),
            encoding="utf-8",
        )
        (temporary / "FINALIZED").write_text(
            json.dumps(
                {
                    "format": GO_BUNDLE_FORMAT,
                    "bundle_id": bundle_id,
                    "manifest_sha256": _sha256(manifest_path),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.rename(final_path)
        return final_path
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


CUTOVER_REPORT_FORMAT = "arthexis-migration-cutover-v1"


def verify_cutover_source(
    source_database: Path,
    *,
    captured_database_sha256: str,
    started_at: datetime,
    rehearsal_root: Path,
) -> dict[str, object]:
    """Prove that no legacy writes occurred after the accepted cutover snapshot."""

    current_sha256 = _sha256(source_database)
    unchanged = current_sha256 == captured_database_sha256
    result = {
        "format": CUTOVER_REPORT_FORMAT,
        "cutover_started_at": started_at.astimezone(timezone.utc).isoformat(),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source_database": str(source_database),
        "captured_database_sha256": captured_database_sha256,
        "current_database_sha256": current_sha256,
        "no_missed_writes_proven": unchanged,
        "decision": "GO" if unchanged else "NO-GO",
        "reason": (
            "source-unchanged-since-capture"
            if unchanged
            else "legacy-source-advanced-after-capture"
        ),
    }
    path = rehearsal_root / "cutover-report.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result["report_path"] = str(path)
    return result

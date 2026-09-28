"""Resource safety policy for legacy migration rehearsals."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass
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

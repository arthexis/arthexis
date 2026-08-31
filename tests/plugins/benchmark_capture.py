"""Capture pytest durations as a standalone benchmark artifact."""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import pytest


@dataclass
class BenchmarkRecord:
    nodeid: str
    duration_seconds: float = 0.0
    file_path: str = ""
    phase_durations: dict[str, float] = field(default_factory=dict)
    status: str = "passed"
    test_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodeid": self.nodeid,
            "file_path": self.file_path,
            "test_name": self.test_name,
            "status": self.status,
            "duration_seconds": round(self.duration_seconds, 6),
            "phase_durations": {
                phase: round(duration, 6)
                for phase, duration in sorted(self.phase_durations.items())
            },
        }


_RECORDS: dict[str, BenchmarkRecord] = {}
_SESSION_START_MONOTONIC = 0.0
_SESSION_STARTED_AT = ""
_STATUS_PRIORITY = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("arthexis benchmark")
    group.addoption(
        "--arthexis-benchmark-json",
        action="store",
        default="",
        help="Write a JSON pytest benchmark artifact to this path.",
    )
    group.addoption(
        "--arthexis-benchmark-top",
        action="store",
        default=20,
        type=int,
        help="Number of slowest tests to include in the benchmark summary.",
    )


def pytest_configure(config: pytest.Config) -> None:
    del config
    global _RECORDS, _SESSION_STARTED_AT, _SESSION_START_MONOTONIC
    _RECORDS = {}
    _SESSION_START_MONOTONIC = time.perf_counter()
    _SESSION_STARTED_AT = datetime.now(timezone.utc).isoformat()


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    record = _RECORDS.setdefault(report.nodeid, BenchmarkRecord(nodeid=report.nodeid))
    location = getattr(report, "location", None)
    if location:
        record.file_path = str(location[0]).replace("\\", "/")
        record.test_name = str(location[2])

    record.phase_durations[report.when] = float(report.duration)
    record.duration_seconds = sum(record.phase_durations.values())
    record.status = _higher_priority_status(record.status, _report_status(report))


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _is_xdist_worker(session.config):
        return

    output = session.config.getoption("--arthexis-benchmark-json")
    if not output:
        return

    top_limit = max(0, int(session.config.getoption("--arthexis-benchmark-top")))
    payload = build_benchmark_payload(
        list(_RECORDS.values()),
        duration_seconds=time.perf_counter() - _SESSION_START_MONOTONIC,
        exitstatus=exitstatus,
        generated_at=datetime.now(timezone.utc).isoformat(),
        started_at=_SESSION_STARTED_AT,
        top_limit=top_limit,
    )
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )


def build_benchmark_payload(
    records: list[BenchmarkRecord],
    *,
    duration_seconds: float,
    exitstatus: int,
    generated_at: str,
    started_at: str,
    top_limit: int,
) -> dict[str, Any]:
    test_payloads = [
        record.to_dict() for record in sorted(records, key=lambda item: item.nodeid)
    ]
    slowest = sorted(
        test_payloads,
        key=lambda item: item["duration_seconds"],
        reverse=True,
    )[:top_limit]
    status_counts = Counter(item["status"] for item in test_payloads)
    groups = _group_summaries(test_payloads)

    return {
        "schema_version": 1,
        "tool": "pytest",
        "generated_at": generated_at,
        "started_at": started_at,
        "exitstatus": exitstatus,
        "summary": {
            "duration_seconds": round(duration_seconds, 6),
            "test_count": len(test_payloads),
            "status_counts": dict(sorted(status_counts.items())),
        },
        "groups": groups,
        "slowest_tests": slowest,
        "tests": test_payloads,
    }


def _group_summaries(test_payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "duration_seconds": 0.0,
            "status_counts": Counter(),
            "test_count": 0,
        }
    )
    for item in test_payloads:
        group_name = _group_name(str(item.get("file_path", "")))
        group = grouped[group_name]
        group["duration_seconds"] += float(item.get("duration_seconds", 0.0))
        group["status_counts"][str(item.get("status", "unknown"))] += 1
        group["test_count"] += 1

    return [
        {
            "name": name,
            "duration_seconds": round(payload["duration_seconds"], 6),
            "test_count": payload["test_count"],
            "status_counts": dict(sorted(payload["status_counts"].items())),
        }
        for name, payload in sorted(
            grouped.items(),
            key=lambda entry: entry[1]["duration_seconds"],
            reverse=True,
        )
    ]


def _group_name(file_path: str) -> str:
    if not file_path:
        return "unknown"
    parts = PurePosixPath(file_path).parts
    if len(parts) >= 2 and parts[0] == "apps":
        return f"apps.{parts[1]}"
    return parts[0] if parts else "unknown"


def _higher_priority_status(current: str, candidate: str) -> str:
    if _STATUS_PRIORITY.get(candidate, 0) > _STATUS_PRIORITY.get(current, 0):
        return candidate
    return current


def _report_status(report: pytest.TestReport) -> str:
    if report.failed and report.when != "call":
        return "error"
    if report.failed:
        return "failed"
    if report.skipped:
        return "skipped"
    return "passed"


def _is_xdist_worker(config: pytest.Config) -> bool:
    return hasattr(config, "workerinput")

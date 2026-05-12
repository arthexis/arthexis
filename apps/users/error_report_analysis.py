"""Helpers for inspecting generated error-report packages."""

from __future__ import annotations

import json
import re
from pathlib import Path
from zipfile import BadZipFile, ZipFile

SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}
RULES = (
    ("critical", "secret_exposure", re.compile(r"(aws_secret_access_key|BEGIN\s+PRIVATE\s+KEY)", re.IGNORECASE), "Potential secret material detected."),
    ("high", "migration", re.compile(r"(migration|django\.db\.utils|OperationalError|ProgrammingError)", re.IGNORECASE), "Migration or database startup failure signals detected."),
    ("high", "startup", re.compile(r"(Traceback \(most recent call last\)|ModuleNotFoundError|ImportError)", re.IGNORECASE), "Python startup traceback detected."),
    ("medium", "service", re.compile(r"(systemd|failed to start|connection refused|timeout)", re.IGNORECASE), "Service-level instability markers detected."),
)


def _load_manifest(zf: ZipFile) -> dict:
    with zf.open("manifest.json") as manifest_fp:
        return json.loads(manifest_fp.read().decode("utf-8"))


def _load_summary(zf: ZipFile) -> str:
    try:
        with zf.open("summary.txt") as summary_fp:
            return summary_fp.read().decode("utf-8", errors="replace")
    except KeyError:
        return ""


def _iter_log_text(zf: ZipFile):
    for name in zf.namelist():
        if not _is_log_entry(name):
            continue
        with zf.open(name) as log_fp:
            yield name, log_fp.read(1024 * 1024).decode("utf-8", errors="replace")


def _is_log_entry(name: str) -> bool:
    if name.endswith(".log"):
        return True
    if name.startswith("logs/") or "/logs/" in name:
        return True
    return name.startswith("external/") and name.endswith((".txt", ".json", ".ndjson"))


def _scan_text_for_rules(source: str, text: str, findings: list[dict], *, summary_suffix: str = "") -> None:
    for severity, category, pattern, message in RULES:
        if not pattern.search(text):
            continue
        findings.append(
            {
                "severity": severity,
                "category": category,
                "message": f"{message}{summary_suffix}",
                "source": source,
            }
        )


def analyze_error_report_package(package_path: Path) -> dict:
    """Return a deterministic analysis payload for an error-report zip file."""
    if not package_path.is_file():
        raise FileNotFoundError(f"Package not found: {package_path}")

    try:
        with ZipFile(package_path) as zf:
            manifest = _load_manifest(zf)
            summary = _load_summary(zf)
            warnings = list(manifest.get("warnings") or [])
            findings = []
            for log_name, log_text in _iter_log_text(zf):
                _scan_text_for_rules(log_name, log_text, findings)
            if summary:
                _scan_text_for_rules("summary.txt", summary, findings, summary_suffix=" (summary.txt)")
    except BadZipFile as exc:
        raise ValueError(f"Invalid zip package: {package_path}") from exc
    except (json.JSONDecodeError, KeyError, OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"Malformed error-report package: {package_path}") from exc

    unique_findings = []
    seen = set()
    for finding in findings:
        key = (finding["severity"], finding["category"], finding["source"])
        if key in seen:
            continue
        seen.add(key)
        unique_findings.append(finding)

    max_rank = max([SEVERITY_ORDER[f["severity"]] for f in unique_findings], default=1)
    max_severity = next(level for level, rank in SEVERITY_ORDER.items() if rank == max_rank)

    return {
        "package": str(package_path),
        "entry_count": len(manifest.get("entries") or []),
        "warnings": warnings,
        "findings": unique_findings,
        "max_severity": max_severity,
        "max_severity_rank": max_rank,
        "risk_score": max_rank * 10 + len(unique_findings),
        "severity_order": SEVERITY_ORDER,
    }

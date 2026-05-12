"""Helpers for inspecting generated error-report packages."""

from __future__ import annotations

import json
import re
from pathlib import Path
from zipfile import BadZipFile, ZipFile

SEVERITY_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
LOG_TEXT_SUFFIXES = (".log", ".txt", ".json", ".ndjson")
LOG_PATH_PART_PATTERN = re.compile(r"(^|[-_.])logs?($|[-_.])", re.IGNORECASE)
PRIVATE_KEY_BLOCK_PATTERN = re.compile(
    r"-----BEGIN\s+PRIVATE\s+KEY-----.*?-----END\s+PRIVATE\s+KEY-----",
    re.IGNORECASE | re.DOTALL,
)
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?P<prefix>"
    r"\b(?:aws_secret_access_key|api[_-]?key|password|secret|token|private[_-]?key)\b"
    r"[\"']?\s*[:=]\s*[\"']?"
    r")(?P<value>[^\s\"',;]+)",
    re.IGNORECASE,
)
SECRET_EXPOSURE_PATTERN = re.compile(
    r"("
    r"BEGIN\s+PRIVATE\s+KEY|"
    r"aws_secret_access_key[\"']?\s*[:=]\s*[\"']?"
    r"(?!(?:\[?redacted\]?|<redacted>|\*+(?:redacted)?\*+))"
    r"\S+"
    r")",
    re.IGNORECASE,
)
RULES = (
    ("critical", "secret_exposure", SECRET_EXPOSURE_PATTERN, "Potential secret material detected."),
    ("high", "migration", re.compile(r"(migration|django\.db\.utils|OperationalError|ProgrammingError)", re.IGNORECASE), "Migration or database startup failure signals detected."),
    ("high", "startup", re.compile(r"(Traceback \(most recent call last\)|ModuleNotFoundError|ImportError)", re.IGNORECASE), "Python startup traceback detected."),
    ("medium", "service", re.compile(r"(systemd|failed to start|connection refused|timeout)", re.IGNORECASE), "Service-level instability markers detected."),
)


def _manifest_list(manifest: dict, field: str, *, string_items: bool = False) -> list:
    value = manifest.get(field)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"manifest.{field} must be a list")
    if string_items and not all(isinstance(item, str) for item in value):
        raise ValueError(f"manifest.{field} entries must be strings")
    return list(value)


def _load_manifest(zf: ZipFile) -> dict:
    with zf.open("manifest.json") as manifest_fp:
        manifest = json.loads(manifest_fp.read().decode("utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json must decode to an object")
    return manifest


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
    normalized_name = name.replace("\\", "/")
    lower_name = normalized_name.lower()
    if lower_name.endswith(".log"):
        return True
    if lower_name.startswith("logs/") or "/logs/" in lower_name:
        return True
    if not lower_name.endswith(LOG_TEXT_SUFFIXES):
        return False
    if lower_name.startswith("external/"):
        return True
    return any(LOG_PATH_PART_PATTERN.search(part) for part in lower_name.split("/"))


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


def redact_sensitive_text(text: str) -> str:
    """Return ``text`` with obvious credential material replaced for reports."""

    redacted = PRIVATE_KEY_BLOCK_PATTERN.sub("[redacted private key]", text)
    return SECRET_ASSIGNMENT_PATTERN.sub(r"\g<prefix>[redacted]", redacted)


def redact_analysis_payload(payload):
    """Return an analysis payload safe for stdout or clear-text JSON files."""

    if isinstance(payload, dict):
        return {
            key: redact_analysis_payload(value)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [redact_analysis_payload(value) for value in payload]
    if isinstance(payload, tuple):
        return [redact_analysis_payload(value) for value in payload]
    if isinstance(payload, str):
        return redact_sensitive_text(payload)
    return payload


def analyze_error_report_package(package_path: Path) -> dict:
    """Return a deterministic analysis payload for an error-report zip file."""
    if not package_path.is_file():
        raise FileNotFoundError(f"Package not found: {package_path}")

    try:
        with ZipFile(package_path) as zf:
            manifest = _load_manifest(zf)
            summary = _load_summary(zf)
            warnings = _manifest_list(manifest, "warnings", string_items=True)
            entry_count = len(_manifest_list(manifest, "entries"))
            findings = []
            for log_name, log_text in _iter_log_text(zf):
                _scan_text_for_rules(log_name, log_text, findings)
            if summary:
                _scan_text_for_rules("summary.txt", summary, findings, summary_suffix=" (summary.txt)")
    except BadZipFile as exc:
        raise ValueError(f"Invalid zip package: {package_path}") from exc
    except (json.JSONDecodeError, KeyError, OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"Malformed error-report package: {package_path}") from exc

    unique_findings = []
    seen = set()
    for finding in findings:
        key = (finding["severity"], finding["category"], finding["source"])
        if key in seen:
            continue
        seen.add(key)
        unique_findings.append(finding)

    max_rank = max([SEVERITY_ORDER[f["severity"]] for f in unique_findings], default=0)
    max_severity = next(level for level, rank in SEVERITY_ORDER.items() if rank == max_rank)

    return {
        "package": str(package_path),
        "entry_count": entry_count,
        "warnings": warnings,
        "findings": unique_findings,
        "max_severity": max_severity,
        "max_severity_rank": max_rank,
        "risk_score": max_rank * 10 + len(unique_findings),
        "severity_order": SEVERITY_ORDER,
    }

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
    r"-----BEGIN(?:\s+[A-Z0-9]+)*\s+PRIVATE\s+KEY-----.*?"
    r"-----END(?:\s+[A-Z0-9]+)*\s+PRIVATE\s+KEY-----",
    re.IGNORECASE | re.DOTALL,
)
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?P<prefix>"
    r"\b(?:"
    r"aws_secret_access_key|"
    r"(?:[A-Za-z0-9]+[_-])*(?:api[_-]?key|password|secret|token|private[_-]?key)"
    r"(?:[_-][A-Za-z0-9]+)*"
    r")\b"
    r"[\"']?\s*[:=]\s*"
    r")"
    r"(?:\"(?P<double_quoted_value>[^\r\n]*)\""
    r"|\'(?P<single_quoted_value>[^\r\n]*)\'"
    r"|(?P<unquoted_value>[^\"'\s;,][^\s;,]*))",
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

MAX_MANIFEST_BYTES = 256 * 1024
MAX_SUMMARY_BYTES = 512 * 1024
MAX_LOG_ENTRY_BYTES = 1024 * 1024
MAX_LOG_ENTRIES_SCANNED = 200
MAX_TOTAL_LOG_BYTES = 16 * 1024 * 1024
MAX_TOTAL_ENTRIES = 2000


def _read_zip_text_limited(zf: ZipFile, name: str, *, limit: int, errors: str = "strict") -> str:
    info = zf.getinfo(name)
    if info.file_size > limit:
        raise ValueError(f"{name} exceeds maximum allowed size")
    with zf.open(info) as fp:
        data = fp.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{name} exceeds maximum allowed size")
    return data.decode("utf-8", errors=errors)


RULES = (
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
    manifest = json.loads(_read_zip_text_limited(zf, "manifest.json", limit=MAX_MANIFEST_BYTES))
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json must decode to an object")
    return manifest


def _load_summary(zf: ZipFile) -> str:
    try:
        return _read_zip_text_limited(zf, "summary.txt", limit=MAX_SUMMARY_BYTES, errors="replace")
    except KeyError:
        return ""


def _iter_log_text(zf: ZipFile):
    scanned_entries = 0
    scanned_bytes = 0
    infos = zf.infolist()
    if len(infos) > MAX_TOTAL_ENTRIES:
        raise ValueError("too many entries in package")
    for info in infos:
        name = info.filename
        if not _is_log_entry(name):
            continue
        if scanned_entries >= MAX_LOG_ENTRIES_SCANNED:
            break
        remaining_bytes = MAX_TOTAL_LOG_BYTES - scanned_bytes
        if remaining_bytes <= 0:
            raise ValueError("log-like entries exceed total scan budget")
        bytes_to_read = min(MAX_LOG_ENTRY_BYTES, remaining_bytes)
        with zf.open(info) as log_fp:
            raw_data = log_fp.read(bytes_to_read + 1)
        if len(raw_data) > bytes_to_read:
            if bytes_to_read < MAX_LOG_ENTRY_BYTES:
                raise ValueError("log-like entries exceed total scan budget")
            raise ValueError(f"{name} exceeds maximum allowed size")
        scanned_entries += 1
        scanned_bytes += len(raw_data)
        text = raw_data.decode("utf-8", errors="replace")
        yield name, text


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
    if SECRET_EXPOSURE_PATTERN.search(text):
        findings.append(
            {
                "severity": "critical",
                "category": "secret_exposure",
                "message": f"Potential secret material detected.{summary_suffix}",
                "source": source,
            }
        )

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

    def _replace_assignment(match: re.Match[str]) -> str:
        if match.group("double_quoted_value") is not None:
            quote = '"'
        elif match.group("single_quoted_value") is not None:
            quote = "'"
        else:
            quote = ""
        return f"{match.group('prefix')}{quote}[redacted]{quote}"

    redacted = PRIVATE_KEY_BLOCK_PATTERN.sub("[redacted private key]", text)
    return SECRET_ASSIGNMENT_PATTERN.sub(_replace_assignment, redacted)


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
        return tuple(redact_analysis_payload(value) for value in payload)
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

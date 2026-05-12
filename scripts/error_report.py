from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from gettext import gettext as _
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

SCHEMA_VERSION = 1
DEFAULT_MAX_LOG_FILES = 30
DEFAULT_MAX_FILE_BYTES = 256 * 1024
DEFAULT_OUTPUT_DIR = Path("work") / "error-reports"
TEXT_SUFFIXES = {".log", ".txt", ".json", ".ndjson", ".lck", ".md", ""}
LOG_SUFFIXES = {".log", ".txt", ".json", ".ndjson"}
STANDARD_LOG_NAMES = {
    "error.log",
    "tests-error.log",
    "celery.log",
    "command.log",
    "install.log",
    "service-start.log",
    "start.log",
    "status.log",
    "upgrade.log",
    "delegated-upgrade.log",
    "watch-upgrade.log",
}
SENSITIVE_NAMES = {
    ".env",
    "arthexis.env",
    "local_settings.py",
    "db.sqlite3",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "rfid-scan.json",
    "rfid-scans.ndjson",
}
SENSITIVE_SUFFIXES = {
    ".db",
    ".dump",
    ".key",
    ".p12",
    ".pem",
    ".pfx",
    ".sqlite",
    ".sqlite3",
}
SENSITIVE_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "backups",
    "cache",
    "media",
    "node_modules",
    "static",
    "venv",
}
SECRET_KEY_RE = re.compile(
    r"(?i)\b([A-Z0-9_.-]*(?:SECRET|TOKEN|PASSWORD|PASS|CREDENTIAL|PRIVATE|API_KEY|ACCESS_KEY|SECRET_KEY)[A-Z0-9_.-]*\b)"
    r"(\s*[:=]\s*)"
    r"([^\s,;]+)"
)
AUTH_HEADER_RE = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
AWS_ACCESS_KEY_RE = re.compile(r"\bA(?:KIA|SIA)[0-9A-Z]{16}\b")
URL_USERINFO_RE = re.compile(
    r"([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^/\s@]+)@",
    re.IGNORECASE,
)
URL_TOKEN_USERINFO_RE = re.compile(
    r"([a-z][a-z0-9+.-]*://)([^/\s:@]+)@",
    re.IGNORECASE,
)
PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
SSO_KEY_RE = re.compile(r"(?i)\bsso-key\s+[^\s]+")


@dataclass(frozen=True)
class ReportConfig:
    base_dir: Path
    output_dir: Path
    since: timedelta | None = None
    max_log_files: int = DEFAULT_MAX_LOG_FILES
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    upload_url: str | None = None
    upload_method: str = "PUT"
    upload_timeout: int = 60
    allow_insecure_upload: bool = False
    dry_run: bool = False


@dataclass
class ReportEntry:
    archive_path: str
    content: bytes
    source_path: str | None = None
    truncated: bool = False


@dataclass
class ReportResult:
    path: Path
    entries: list[ReportEntry]
    warnings: list[str] = field(default_factory=list)
    dry_run: bool = False


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return cleaned or "unknown"


def parse_duration(value: str) -> timedelta:
    match = re.fullmatch(r"\s*(\d+)\s*([smhdw])\s*", value)
    if not match:
        raise argparse.ArgumentTypeError(
            _("duration must use a suffix: s, m, h, d, or w; for example 12h or 7d")
        )
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "s":
        return timedelta(seconds=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "d":
        return timedelta(days=amount)
    return timedelta(weeks=amount)


def redact_text(text: str) -> str:
    redacted = PRIVATE_KEY_BLOCK_RE.sub("<redacted:private-key>", text)
    redacted = URL_USERINFO_RE.sub(r"\1<redacted>@", redacted)
    redacted = URL_TOKEN_USERINFO_RE.sub(r"\1<redacted>@", redacted)
    redacted = AUTH_HEADER_RE.sub(lambda match: f"{match.group(1)} <redacted>", redacted)
    redacted = AWS_ACCESS_KEY_RE.sub("<redacted:aws-access-key>", redacted)
    redacted = SSO_KEY_RE.sub("sso-key <redacted>", redacted)
    redacted = SECRET_KEY_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}<redacted>", redacted)
    return redacted


def is_sensitive_path(path: Path, *, base_dir: Path | None = None) -> bool:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if name in SENSITIVE_NAMES or suffix in SENSITIVE_SUFFIXES:
        return True
    relative = path_relative_to(path, base_dir) if base_dir is not None else None
    if relative is None:
        return False
    parts = {part.lower() for part in relative.parts}
    return bool(parts & SENSITIVE_PARTS)


def should_read_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES


def read_text_tail(path: Path, max_bytes: int) -> tuple[str, bool]:
    size = path.stat().st_size
    truncated = size > max_bytes
    with path.open("rb") as handle:
        if truncated:
            handle.seek(-max_bytes, os.SEEK_END)
        payload = handle.read(max_bytes)
    text = payload.decode("utf-8", errors="replace")
    if truncated:
        text = f"[truncated to last {max_bytes} bytes from {size} total bytes]\n{text}"
    return redact_text(text), truncated


def normalize_archive_path(path: str | Path) -> str:
    raw = Path(path).as_posix().lstrip("/")
    parts = [part for part in raw.split("/") if part not in {"", ".", ".."}]
    return "/".join(parts)


def path_relative_to(path: Path, base_dir: Path) -> Path | None:
    try:
        return path.resolve().relative_to(base_dir.resolve())
    except ValueError:
        return None


class ReportBuilder:
    def __init__(self, config: ReportConfig, created_at: datetime) -> None:
        self.config = config
        self.created_at = created_at
        self.entries: list[ReportEntry] = []
        self.warnings: list[str] = []
        self._archive_paths: set[str] = set()

    def add_text(self, archive_path: str, text: str, source_path: Path | None = None) -> None:
        self.add_bytes(
            archive_path,
            redact_text(text).encode("utf-8"),
            source_path=source_path,
            truncated=False,
        )

    def add_json(self, archive_path: str, payload: object) -> None:
        self.add_text(archive_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def add_file_tail(self, archive_path: str, path: Path) -> None:
        if path.is_symlink():
            self.warnings.append(f"Skipped symlink: {path}")
            return
        if not path.is_file():
            return
        if is_sensitive_path(path, base_dir=self.config.base_dir):
            self.warnings.append(f"Skipped sensitive path: {path}")
            return
        if not should_read_text(path):
            self.warnings.append(f"Skipped non-text file: {path}")
            return
        try:
            text, truncated = read_text_tail(path, self.config.max_file_bytes)
        except OSError as exc:
            self.warnings.append(f"Could not read {path}: {exc}")
            return
        self.add_bytes(
            archive_path,
            text.encode("utf-8"),
            source_path=path,
            truncated=truncated,
        )

    def add_bytes(
        self,
        archive_path: str,
        content: bytes,
        *,
        source_path: Path | None = None,
        truncated: bool = False,
    ) -> None:
        safe_archive_path = self._dedupe_archive_path(normalize_archive_path(archive_path))
        self.entries.append(
            ReportEntry(
                archive_path=safe_archive_path,
                content=content,
                source_path=str(source_path) if source_path else None,
                truncated=truncated,
            )
        )

    def _dedupe_archive_path(self, archive_path: str) -> str:
        if archive_path not in self._archive_paths:
            self._archive_paths.add(archive_path)
            return archive_path
        stem, suffix = os.path.splitext(archive_path)
        counter = 2
        while True:
            candidate = f"{stem}-{counter}{suffix}"
            if candidate not in self._archive_paths:
                self._archive_paths.add(candidate)
                return candidate
            counter += 1


def run_command(args: list[str], *, cwd: Path, timeout: int = 10) -> str:
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd),
            check=False,
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return _("Command not available: {command}").format(command=args[0]) + "\n"
    except subprocess.TimeoutExpired:
        return (
            _("Command timed out after {timeout}s: {command}").format(
                timeout=timeout,
                command=" ".join(args),
            )
            + "\n"
        )

    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    output = stdout
    if stderr:
        output += ("\n[stderr]\n" if output else "[stderr]\n") + stderr
    output += f"\n[exit_code] {completed.returncode}\n"
    return redact_text(output)


def read_optional_text(path: Path) -> str:
    try:
        return redact_text(path.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return ""


def discover_log_dirs(base_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    env_log_dir = os.environ.get("ARTHEXIS_LOG_DIR")
    if env_log_dir:
        candidates.append(Path(env_log_dir))
    candidates.append(base_dir / "logs")

    seen: set[Path] = set()
    existing: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        existing.append(resolved)
    return existing


def collect_log_files(base_dir: Path, config: ReportConfig, cutoff: float | None) -> list[Path]:
    files: list[Path] = []
    for log_dir in discover_log_dirs(base_dir):
        for path in log_dir.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            if path.suffix.lower() not in LOG_SUFFIXES:
                continue
            if is_sensitive_path(path, base_dir=base_dir):
                continue
            if cutoff is not None and path.name not in STANDARD_LOG_NAMES:
                try:
                    if path.stat().st_mtime < cutoff:
                        continue
                except OSError:
                    continue
            files.append(path)

    def sort_key(path: Path) -> tuple[int, float, str]:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        standard_rank = 0 if path.name in STANDARD_LOG_NAMES else 1
        return (standard_rank, -mtime, str(path))

    unique: dict[Path, Path] = {}
    for path in files:
        try:
            unique[path.resolve()] = path
        except OSError:
            continue
    return sorted(unique.values(), key=sort_key)[: config.max_log_files]


def archive_path_for_source(path: Path, base_dir: Path, prefix: str = "") -> str:
    relative = path_relative_to(path, base_dir)
    if relative is None:
        relative = Path("external") / sanitize_filename(str(path.parent)) / path.name
    if prefix:
        relative = Path(prefix) / relative
    return normalize_archive_path(relative)


def collect_locks(builder: ReportBuilder) -> None:
    lock_dir = builder.config.base_dir / ".locks"
    if not lock_dir.is_dir():
        builder.add_text("arthexis/locks.txt", "No .locks directory found.\n")
        return
    found = False
    for path in sorted(lock_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".lck", ".json", ".txt", ".log", ""}:
            continue
        if is_sensitive_path(path, base_dir=builder.config.base_dir):
            continue
        found = True
        relative = path.relative_to(lock_dir)
        builder.add_file_tail(Path("arthexis/locks") / relative, path)
    if not found:
        builder.add_text("arthexis/locks.txt", "No readable lock files found.\n")


def collect_logs(builder: ReportBuilder, cutoff: float | None) -> None:
    logs = collect_log_files(builder.config.base_dir, builder.config, cutoff)
    if not logs:
        builder.add_text("logs/README.txt", "No readable log files found.\n")
        return
    for path in logs:
        builder.add_file_tail(archive_path_for_source(path, builder.config.base_dir), path)


def status_snapshot(base_dir: Path, log_dirs: Iterable[Path]) -> str:
    log_dir_list = list(log_dirs)
    lock_dir = base_dir / ".locks"
    service = read_optional_text(lock_dir / "service.lck")
    role = read_optional_text(lock_dir / "role.lck") or os.environ.get("NODE_ROLE", "")
    backend_port = read_optional_text(lock_dir / "backend_port.lck")
    auto_upgrade = (lock_dir / "auto-upgrade.lck").exists() or (
        lock_dir / "auto_upgrade.lck"
    ).exists()
    upgrade_in_progress = (lock_dir / "upgrade_in_progress.lck").exists()
    installed = (base_dir / ".venv").is_dir()
    version = read_optional_text(base_dir / "VERSION")
    revision = read_optional_text(base_dir / ".revision")
    lines = [
        "Arthexis error-report status snapshot",
        "",
        f"Installed venv: {installed}",
        f"Version: {version or 'unknown'}",
        f"Revision marker: {revision or 'not present'}",
        f"Service: {service or 'not configured'}",
        f"Node role: {role or 'not configured'}",
        f"Backend port: {backend_port or 'not configured'}",
        f"Auto-upgrade lock present: {auto_upgrade}",
        f"Upgrade in progress lock present: {upgrade_in_progress}",
        "Log directories:",
    ]
    for log_dir in log_dir_list:
        lines.append(f"- {log_dir}")
    if not log_dir_list:
        lines.append("- none found")
    lines.extend(
        [
            "",
            "Note: status.sh is not invoked by error-report because it can update startup locks.",
        ]
    )
    return "\n".join(lines) + "\n"


def collect_report_entries(config: ReportConfig, created_at: datetime) -> ReportBuilder:
    base_dir = config.base_dir
    builder = ReportBuilder(config, created_at)
    log_dirs = discover_log_dirs(base_dir)
    cutoff = None
    if config.since is not None:
        cutoff = time.time() - config.since.total_seconds()

    builder.add_text(
        "system/platform.txt",
        "\n".join(
            [
                f"Hostname: {socket.gethostname()}",
                f"Platform: {platform.platform()}",
                f"System: {platform.system()}",
                f"Release: {platform.release()}",
                f"Machine: {platform.machine()}",
                f"Processor: {platform.processor()}",
            ]
        )
        + "\n",
    )
    builder.add_text(
        "system/python.txt",
        "\n".join(
            [
                f"Executable: {sys.executable}",
                f"Version: {sys.version}",
                f"Prefix: {sys.prefix}",
            ]
        )
        + "\n",
    )
    builder.add_text(
        "system/environment.txt",
        "\n".join(
            [
                f"ARTHEXIS_LOG_DIR set: {bool(os.environ.get('ARTHEXIS_LOG_DIR'))}",
                f"NODE_ROLE set: {bool(os.environ.get('NODE_ROLE'))}",
                f"PATH entries: {len(os.environ.get('PATH', '').split(os.pathsep)) if os.environ.get('PATH') else 0}",
            ]
        )
        + "\n",
    )
    builder.add_text("arthexis/status.txt", status_snapshot(base_dir, log_dirs))
    builder.add_text(
        "arthexis/git.txt",
        "\n".join(
            [
                "$ git rev-parse HEAD",
                run_command(["git", "rev-parse", "HEAD"], cwd=base_dir),
                "$ git status --short --branch",
                run_command(["git", "status", "--short", "--branch"], cwd=base_dir),
                "$ git remote -v",
                run_command(["git", "remote", "-v"], cwd=base_dir),
            ]
        ),
    )
    version_path = base_dir / "VERSION"
    if version_path.is_file():
        builder.add_file_tail("arthexis/VERSION", version_path)

    collect_locks(builder)
    collect_logs(builder, cutoff)
    return builder


def build_manifest(
    config: ReportConfig,
    created_at: datetime,
    report_path: Path,
    entries: list[ReportEntry],
    warnings: list[str],
) -> dict[str, object]:
    entry_records = []
    for entry in entries:
        digest = hashlib.sha256(entry.content).hexdigest()
        entry_records.append(
            {
                "path": entry.archive_path,
                "bytes": len(entry.content),
                "sha256": digest,
                "source": entry.source_path,
                "truncated": entry.truncated,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": created_at.isoformat(),
        "generated_by": "scripts/error_report.py",
        "report_name": report_path.name,
        "base_dir": str(config.base_dir),
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python_version": sys.version,
        "options": {
            "since_seconds": int(config.since.total_seconds()) if config.since else None,
            "max_log_files": config.max_log_files,
            "max_file_bytes": config.max_file_bytes,
            "upload_requested": bool(config.upload_url),
            "upload_method": config.upload_method,
        },
        "exclusions": [
            "environment files",
            "databases and dumps",
            "private keys and certificates",
            "media/static/cache/venv trees",
            "backups and broad data directories",
        ],
        "warnings": warnings,
        "entries": entry_records,
    }


def build_summary(manifest: dict[str, object]) -> str:
    entries = manifest.get("entries", [])
    warnings = manifest.get("warnings", [])
    return "\n".join(
        [
            "Arthexis Error Report",
            "",
            f"Created at: {manifest['created_at']}",
            f"Hostname: {manifest['hostname']}",
            f"Platform: {manifest['platform']}",
            f"Entries: {len(entries) if isinstance(entries, list) else 0}",
            f"Warnings: {len(warnings) if isinstance(warnings, list) else 0}",
            "",
            "This package excludes databases, env files, private keys, backups, media, static files, caches, and venvs.",
            "Text content and command output are redacted for common secret-bearing values.",
        ]
    ) + "\n"


def report_path_for(config: ReportConfig, created_at: datetime) -> Path:
    stamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    hostname = sanitize_filename(socket.gethostname())
    return config.output_dir / f"arthexis-error-report-{hostname}-{stamp}.zip"


def build_report(config: ReportConfig) -> ReportResult:
    created_at = utc_now()
    report_path = report_path_for(config, created_at)
    builder = collect_report_entries(config, created_at)
    manifest = build_manifest(config, created_at, report_path, builder.entries, builder.warnings)
    manifest_entry = ReportEntry(
        archive_path="manifest.json",
        content=(json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    summary_entry = ReportEntry(
        archive_path="summary.txt",
        content=build_summary(manifest).encode("utf-8"),
    )
    entries = [manifest_entry, summary_entry, *builder.entries]

    if config.dry_run:
        return ReportResult(path=report_path, entries=entries, warnings=builder.warnings, dry_run=True)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(report_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            archive.writestr(entry.archive_path, entry.content)
    return ReportResult(path=report_path, entries=entries, warnings=builder.warnings)


def validate_upload_url(url: str, allow_insecure: bool) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(_("upload URL must be an http(s) URL with a host"))
    if parsed.scheme != "https" and not allow_insecure:
        raise ValueError(_("upload URL must use https unless --allow-insecure-upload is set"))


def upload_report(path: Path, url: str, *, method: str = "PUT", timeout: int = 60, allow_insecure: bool = False) -> int:
    validate_upload_url(url, allow_insecure)
    method = method.upper()
    if method not in {"PUT", "POST"}:
        raise ValueError(_("upload method must be PUT or POST"))
    data = path.read_bytes()
    request = Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/zip",
            "Content-Length": str(len(data)),
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return int(getattr(response, "status", response.getcode()))


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError(_("value must be greater than zero"))
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=_("Build an Arthexis diagnostic error-report zip."))
    parser.add_argument("--base-dir", default=Path.cwd(), type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--since", type=parse_duration)
    parser.add_argument("--max-log-files", type=positive_int, default=DEFAULT_MAX_LOG_FILES)
    parser.add_argument("--max-file-bytes", type=positive_int, default=DEFAULT_MAX_FILE_BYTES)
    parser.add_argument("--upload-url")
    parser.add_argument("--upload-method", default="PUT", choices=["PUT", "POST", "put", "post"])
    parser.add_argument("--upload-timeout", type=positive_int, default=60)
    parser.add_argument("--allow-insecure-upload", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--send-upstream")
    parser.add_argument("--upstream-queue-dir", type=Path, default=None)
    return parser


def _queue_upstream(path: Path, queue_dir: Path) -> Path:
    queue_dir.mkdir(parents=True, exist_ok=True)
    queued = queue_dir / path.name
    if queued.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        queued = queue_dir / f"{path.stem}-{stamp}{path.suffix}"
    shutil.copy2(path, queued)
    return queued


def _flush_upstream_queue(queue_dir: Path, upload_url: str, *, method: str, timeout: int, allow_insecure: bool) -> list[Path]:
    sent = []
    if not queue_dir.exists():
        return sent
    for candidate in sorted(queue_dir.glob("*.zip")):
        upload_report(candidate, upload_url, method=method, timeout=timeout, allow_insecure=allow_insecure)
        try:
            candidate.unlink(missing_ok=True)
        except OSError as exc:
            print(
                _("Warning: could not delete queued file {path} after upload: {error}").format(
                    path=candidate,
                    error=exc,
                ),
                file=sys.stderr,
            )
        sent.append(candidate)
    return sent


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    base_dir = args.base_dir.expanduser().resolve()
    output_dir = args.output_dir
    if output_dir is None:
        output_dir = base_dir / DEFAULT_OUTPUT_DIR
    elif not output_dir.is_absolute():
        output_dir = base_dir / output_dir

    upstream_queue_dir = args.upstream_queue_dir or (base_dir / "work" / "error-reports" / "upstream-queue")
    upstream_url = (args.send_upstream or "").strip()

    config = ReportConfig(
        base_dir=base_dir,
        output_dir=output_dir,
        since=args.since,
        max_log_files=args.max_log_files,
        max_file_bytes=args.max_file_bytes,
        upload_url=args.upload_url,
        upload_method=args.upload_method.upper(),
        upload_timeout=args.upload_timeout,
        allow_insecure_upload=args.allow_insecure_upload,
        dry_run=args.dry_run,
    )

    try:
        result = build_report(config)
    except OSError as exc:
        print(_("error-report failed: {error}").format(error=exc), file=sys.stderr)
        return 1

    if result.dry_run:
        print(_("Would create: {path}").format(path=result.path))
        for entry in result.entries:
            source = f" <- {entry.source_path}" if entry.source_path else ""
            print(f"- {entry.archive_path}{source}")
        if config.upload_url:
            print(_("Upload skipped during dry run."))
        return 0

    print(_("Created error report: {path}").format(path=result.path))
    if result.warnings:
        print(_("Warnings: {count}").format(count=len(result.warnings)))

    if upstream_url:
        try:
            flushed = _flush_upstream_queue(upstream_queue_dir, upstream_url, method=config.upload_method, timeout=config.upload_timeout, allow_insecure=config.allow_insecure_upload)
            if flushed:
                print(_("Flushed queued upstream uploads: {count}").format(count=len(flushed)))
        except (HTTPError, URLError, OSError, ValueError):
            pass

    if config.upload_url:
        try:
            status = upload_report(
                result.path,
                config.upload_url,
                method=config.upload_method,
                timeout=config.upload_timeout,
                allow_insecure=config.allow_insecure_upload,
            )
        except (HTTPError, URLError, OSError, ValueError) as exc:
            print(_("Upload failed; local zip remains at {path}: {error}").format(path=result.path, error=exc), file=sys.stderr)
            return 2
        print(_("Uploaded error report: HTTP {status}").format(status=status))

    if upstream_url:
        try:
            status = upload_report(result.path, upstream_url, method=config.upload_method, timeout=config.upload_timeout, allow_insecure=config.allow_insecure_upload)
            print(_("Sent upstream error report: HTTP {status}").format(status=status))
        except (HTTPError, URLError, OSError, ValueError):
            queued = _queue_upstream(result.path, upstream_queue_dir)
            print(_("Upstream upload unavailable; queued report at {path}").format(path=queued), file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from celery import shared_task
from django.conf import settings

from apps.emails import mailer
from apps.emails.utils import resolve_recipient_fallbacks
from apps.nodes.models import Node
from utils.loggers.rotation import TRANSACTIONAL_LOG_RETENTION_DAYS

logger = logging.getLogger(__name__)

MAX_LOG_RETENTION_DAYS = 730
DISK_USAGE_ALERT_PERCENT = 80
AGGRESSIVE_RETENTION_STEPS_DAYS = (90, 30, 7, 1)
MANAGED_LOG_BASENAMES = {
    "celery.log",
    "cp_forwarder.log",
    "error.log",
    "lcd-screen.log",
    "page_misses.log",
    "rfid.log",
    "register_local_node.log",
    "register_visitor_node.log",
    "tests-celery.log",
    "tests-cp_forwarder.log",
    "tests-error.log",
    "tests-page_misses.log",
    "tests-rfid.log",
    "tests.log",
}
LOG_ARTIFACT_SUFFIXES = {".log", ".ndjson"}
SESSION_LOG_SUFFIXES = {".json"}
SESSION_LOG_DIR_NAMES = {"sessions"}
SESSION_LOG_TAIL_CHUNK_BYTES = 4096


@dataclass(frozen=True)
class RetentionResult:
    deleted_files: int
    deleted_bytes: int
    disk_percent: float
    alert_sent: bool


@dataclass(frozen=True)
class LogCandidate:
    path: Path
    modified: datetime
    size: int


def _disk_usage_percent(path: Path) -> float:
    usage = shutil.disk_usage(path)
    if usage.total <= 0:
        return 0.0
    return (usage.used / usage.total) * 100


def _is_log_artifact(path: Path, *, log_dir: Path) -> bool:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if suffix in LOG_ARTIFACT_SUFFIXES:
        return True
    if ".log." in name or ".ndjson." in name:
        return True
    if suffix not in SESSION_LOG_SUFFIXES:
        return False
    try:
        relative_parts = path.relative_to(log_dir).parts
    except ValueError:
        return False
    return bool(relative_parts) and relative_parts[0] in SESSION_LOG_DIR_NAMES


def _is_session_log_artifact(path: Path, *, log_dir: Path) -> bool:
    """Return True when *path* is a session JSON artifact under LOG_DIR/sessions."""

    if path.suffix.lower() not in SESSION_LOG_SUFFIXES:
        return False
    try:
        relative_parts = path.relative_to(log_dir).parts
    except ValueError:
        return False
    return bool(relative_parts) and relative_parts[0] in SESSION_LOG_DIR_NAMES


def _is_in_progress_session_log(
    path: Path,
    *,
    log_dir: Path,
    now: datetime | None = None,
) -> bool:
    """Return True when a recent session JSON array appears to still be open.

    Session writers create JSON arrays incrementally, so recent files without a
    closing bracket are preserved. Files older than the normal retention horizon
    are not treated as active, which lets orphaned partial logs age out.
    """

    if not _is_session_log_artifact(path, log_dir=log_dir):
        return False
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return True
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=MAX_LOG_RETENTION_DAYS)
    if modified < cutoff:
        return False
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            position = handle.tell()
            while position > 0:
                chunk_size = min(SESSION_LOG_TAIL_CHUNK_BYTES, position)
                position -= chunk_size
                handle.seek(position)
                chunk = handle.read(chunk_size).rstrip()
                if chunk:
                    return not chunk.endswith(b"]")
    except OSError:
        return True
    return True


def _is_active_log_file(path: Path) -> bool:
    return path.suffix.lower() == ".log" and ".log." not in path.name.lower()


def _is_managed_transactional_log(path: Path, *, archive_dir: Path) -> bool:
    name = path.name
    if name in MANAGED_LOG_BASENAMES:
        return True

    if path.parent == archive_dir:
        for base in MANAGED_LOG_BASENAMES:
            if name.startswith(f"{base}."):
                return True

    return False


def _is_protected_active_log(path: Path, *, archive_dir: Path, log_dir: Path) -> bool:
    if _is_in_progress_session_log(path, log_dir=log_dir):
        return True
    return path.parent != archive_dir and (
        _is_active_log_file(path) or path.name in MANAGED_LOG_BASENAMES
    )


def _retention_days_for(path: Path, *, archive_dir: Path) -> int:
    if _is_managed_transactional_log(path, archive_dir=archive_dir):
        return TRANSACTIONAL_LOG_RETENTION_DAYS
    return MAX_LOG_RETENTION_DAYS


def _collect_log_candidates(log_dir: Path) -> list[LogCandidate]:
    candidates: list[LogCandidate] = []
    for path in log_dir.rglob("*"):
        if not path.is_file() or not _is_log_artifact(path, log_dir=log_dir):
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        candidates.append(
            LogCandidate(
                path=path,
                modified=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
                size=st.st_size,
            )
        )
    return candidates


def _delete_candidates(log_dir: Path, *, max_age_days: int) -> tuple[int, int]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    archive_dir = log_dir / "archive"
    deleted_files = 0
    deleted_bytes = 0

    for candidate in _collect_log_candidates(log_dir):
        if _is_protected_active_log(
            candidate.path,
            archive_dir=archive_dir,
            log_dir=log_dir,
        ):
            continue
        if candidate.modified >= cutoff:
            continue
        try:
            candidate.path.unlink()
        except OSError:
            continue
        deleted_files += 1
        deleted_bytes += candidate.size

    return deleted_files, deleted_bytes


def _trim_with_policy(log_dir: Path) -> tuple[int, int]:
    deleted_files = 0
    deleted_bytes = 0

    now = datetime.now(timezone.utc)
    archive_dir = log_dir / "archive"
    for candidate in _collect_log_candidates(log_dir):
        if _is_protected_active_log(
            candidate.path,
            archive_dir=archive_dir,
            log_dir=log_dir,
        ):
            continue
        retention_days = _retention_days_for(candidate.path, archive_dir=archive_dir)
        cutoff = now - timedelta(days=retention_days)
        if candidate.modified >= cutoff:
            continue
        try:
            candidate.path.unlink()
        except OSError:
            continue
        deleted_files += 1
        deleted_bytes += candidate.size

    return deleted_files, deleted_bytes


def _send_disk_pressure_alert(*, before_percent: float, after_percent: float) -> bool:
    recipients, _ = resolve_recipient_fallbacks([], owner=None)
    if not recipients:
        logger.warning("Disk pressure alert skipped: no admin recipients resolved")
        return False

    if not mailer.can_send_email():
        logger.warning("Disk pressure alert skipped: email backend is not configured")
        return False

    subject = "Arthexis disk pressure: log trimming did not recover enough space"
    body = (
        "Arthexis detected sustained disk pressure after aggressive log trimming.\n\n"
        f"Disk usage before aggressive trimming: {before_percent:.1f}%\n"
        f"Disk usage after aggressive trimming: {after_percent:.1f}%\n"
        f"Threshold: {DISK_USAGE_ALERT_PERCENT}%\n\n"
        "Please review node storage usage and remove non-log artifacts if needed."
    )

    node = Node.get_local()
    try:
        if node is not None:
            node.send_mail(subject, body, recipients)
        else:
            mailer.send(subject=subject, message=body, recipient_list=recipients)
    except Exception:
        logger.exception("Disk pressure alert failed while sending email")
        return False
    return True


def _run_log_retention() -> RetentionResult:
    log_dir = Path(settings.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)

    deleted_files, deleted_bytes = _trim_with_policy(log_dir)

    disk_percent = _disk_usage_percent(log_dir)
    alert_sent = False
    if disk_percent >= DISK_USAGE_ALERT_PERCENT:
        before_percent = disk_percent
        for days in AGGRESSIVE_RETENTION_STEPS_DAYS:
            extra_files, extra_bytes = _delete_candidates(log_dir, max_age_days=days)
            deleted_files += extra_files
            deleted_bytes += extra_bytes
            disk_percent = _disk_usage_percent(log_dir)
            if disk_percent < DISK_USAGE_ALERT_PERCENT:
                break
        if disk_percent >= DISK_USAGE_ALERT_PERCENT:
            alert_sent = _send_disk_pressure_alert(
                before_percent=before_percent,
                after_percent=disk_percent,
            )

    logger.info(
        "Log retention completed: deleted_files=%s deleted_bytes=%s disk_usage=%.1f%% alert_sent=%s",
        deleted_files,
        deleted_bytes,
        disk_percent,
        alert_sent,
    )
    return RetentionResult(
        deleted_files=deleted_files,
        deleted_bytes=deleted_bytes,
        disk_percent=disk_percent,
        alert_sent=alert_sent,
    )


@shared_task(name="apps.core.tasks.log_retention.enforce_log_retention")
def enforce_log_retention() -> dict[str, int | float | bool]:
    """Enforce default and emergency log retention rules for unattended nodes."""

    result = _run_log_retention()
    return {
        "alert_sent": result.alert_sent,
        "deleted_bytes": result.deleted_bytes,
        "deleted_files": result.deleted_files,
        "disk_percent": round(result.disk_percent, 2),
    }

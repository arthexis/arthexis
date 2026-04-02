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
    "page_misses.log",
    "rfid.log",
    "tests-celery.log",
    "tests-error.log",
    "tests-page_misses.log",
    "tests.log",
}


@dataclass(frozen=True)
class RetentionResult:
    deleted_files: int
    deleted_bytes: int
    disk_percent: float
    alert_sent: bool


def _disk_usage_percent(path: Path) -> float:
    usage = shutil.disk_usage(path)
    if usage.total <= 0:
        return 0.0
    return (usage.used / usage.total) * 100


def _is_log_artifact(path: Path) -> bool:
    name = path.name.lower()
    if ".log." in name:
        return True
    return path.suffix.lower() in {".gz", ".json", ".log", ".txt"}


def _is_managed_transactional_log(path: Path, *, log_dir: Path) -> bool:
    name = path.name
    if name in MANAGED_LOG_BASENAMES:
        return True

    archive_dir = log_dir / "archive"
    if path.parent == archive_dir:
        for base in MANAGED_LOG_BASENAMES:
            if name.startswith(f"{base}."):
                return True

    return False


def _retention_days_for(path: Path, *, log_dir: Path) -> int:
    if _is_managed_transactional_log(path, log_dir=log_dir):
        return TRANSACTIONAL_LOG_RETENTION_DAYS
    return MAX_LOG_RETENTION_DAYS


def _delete_candidates(log_dir: Path, *, max_age_days: int) -> tuple[int, int]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    deleted_files = 0
    deleted_bytes = 0

    for path in sorted(log_dir.rglob("*")):
        if not path.is_file() or not _is_log_artifact(path):
            continue
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if modified >= cutoff:
            continue
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            continue
        deleted_files += 1
        deleted_bytes += size

    return deleted_files, deleted_bytes


def _trim_with_policy(log_dir: Path) -> tuple[int, int]:
    deleted_files = 0
    deleted_bytes = 0

    now = datetime.now(timezone.utc)
    for path in sorted(log_dir.rglob("*")):
        if not path.is_file() or not _is_log_artifact(path):
            continue
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        retention_days = _retention_days_for(path, log_dir=log_dir)
        cutoff = now - timedelta(days=retention_days)
        if modified >= cutoff:
            continue
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            continue
        deleted_files += 1
        deleted_bytes += size

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
    if node is not None:
        node.send_mail(subject, body, recipients)
    else:
        mailer.send(subject=subject, message=body, recipient_list=recipients)
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

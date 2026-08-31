from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from django.utils import timezone

from apps.core.auto_upgrade import append_auto_upgrade_log


logger = logging.getLogger(__name__)

AUTO_UPGRADE_SKIP_LOCK_NAME = "auto_upgrade_skip_revisions.lck"
AUTO_UPGRADE_NETWORK_FAILURE_LOCK_NAME = "auto_upgrade_network_failures.lck"
AUTO_UPGRADE_FAILURE_LOCK_NAME = "auto_upgrade_failures.lck"
AUTO_UPGRADE_RECENCY_LOCK_NAME = "auto_upgrade_last_run.lck"


def _recency_lock_path(base_dir: Path) -> Path:
    return base_dir / ".locks" / AUTO_UPGRADE_RECENCY_LOCK_NAME


def _record_auto_upgrade_timestamp(base_dir: Path) -> None:
    lock_path = _recency_lock_path(base_dir)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(timezone.now().isoformat(), encoding="utf-8")
    except OSError:
        logger.warning("Failed to update auto-upgrade recency lockfile")


def _auto_upgrade_ran_recently(base_dir: Path, interval_minutes: int) -> bool:
    lock_path = _recency_lock_path(base_dir)
    try:
        raw_value = lock_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return False
    except OSError:
        logger.warning("Failed to read auto-upgrade recency lockfile")
        return False

    if not raw_value:
        return False

    try:
        recorded_time = datetime.fromisoformat(raw_value)
    except ValueError:
        logger.warning(
            "Invalid auto-upgrade recency lockfile contents: %s", raw_value
        )
        return False

    if timezone.is_naive(recorded_time):
        recorded_time = timezone.make_aware(recorded_time)

    now = timezone.now()
    if recorded_time > now:
        logger.warning(
            "Auto-upgrade recency lockfile is in the future; ignoring timestamp"
        )
        return False

    return recorded_time > now - timedelta(minutes=interval_minutes)


def _skip_lock_path(base_dir: Path) -> Path:
    return base_dir / ".locks" / AUTO_UPGRADE_SKIP_LOCK_NAME


def _load_skipped_revisions(base_dir: Path) -> set[str]:
    skip_file = _skip_lock_path(base_dir)
    try:
        return {
            line.strip()
            for line in skip_file.read_text().splitlines()
            if line.strip()
        }
    except FileNotFoundError:
        return set()
    except OSError:
        logger.warning("Failed to read auto-upgrade skip lockfile")
        return set()


def _add_skipped_revision(base_dir: Path, revision: str) -> None:
    if not revision:
        return

    skip_file = _skip_lock_path(base_dir)
    try:
        skip_file.parent.mkdir(parents=True, exist_ok=True)
        existing = _load_skipped_revisions(base_dir)
        if revision in existing:
            return
        with skip_file.open("a", encoding="utf-8") as fh:
            fh.write(f"{revision}\n")
        append_auto_upgrade_log(
            base_dir, f"Recorded blocked revision {revision} for auto-upgrade"
        )
    except OSError:
        logger.warning(
            "Failed to update auto-upgrade skip lockfile with revision %s", revision
        )


def _network_failure_lock_path(base_dir: Path) -> Path:
    return base_dir / ".locks" / AUTO_UPGRADE_NETWORK_FAILURE_LOCK_NAME


def _read_network_failure_count(base_dir: Path) -> int:
    lock_path = _network_failure_lock_path(base_dir)
    try:
        raw_value = lock_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return 0
    except OSError:
        logger.warning("Failed to read auto-upgrade network failure lockfile")
        return 0
    if not raw_value:
        return 0
    try:
        return int(raw_value)
    except ValueError:
        logger.warning(
            "Invalid auto-upgrade network failure lockfile contents: %s", raw_value
        )
        return 0


def _write_network_failure_count(base_dir: Path, count: int) -> None:
    lock_path = _network_failure_lock_path(base_dir)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(str(count), encoding="utf-8")
    except OSError:
        logger.warning("Failed to update auto-upgrade network failure lockfile")


def _reset_network_failure_count(base_dir: Path) -> None:
    lock_path = _network_failure_lock_path(base_dir)
    try:
        if lock_path.exists():
            lock_path.unlink()
    except OSError:
        logger.warning("Failed to remove auto-upgrade network failure lockfile")


def _auto_upgrade_failure_lock_path(base_dir: Path) -> Path:
    return base_dir / ".locks" / AUTO_UPGRADE_FAILURE_LOCK_NAME


def _read_auto_upgrade_failure_count(base_dir: Path) -> int:
    lock_path = _auto_upgrade_failure_lock_path(base_dir)
    try:
        raw_value = lock_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return 0
    except OSError:
        logger.warning("Failed to read auto-upgrade failure lockfile")
        return 0
    if not raw_value:
        return 0
    try:
        return int(raw_value)
    except ValueError:
        logger.warning(
            "Invalid auto-upgrade failure lockfile contents: %s", raw_value
        )
        return 0


def _write_auto_upgrade_failure_count(base_dir: Path, count: int) -> None:
    lock_path = _auto_upgrade_failure_lock_path(base_dir)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(str(count), encoding="utf-8")
    except OSError:
        logger.warning("Failed to update auto-upgrade failure lockfile")


def _reset_auto_upgrade_failure_count(base_dir: Path) -> None:
    lock_path = _auto_upgrade_failure_lock_path(base_dir)
    try:
        if lock_path.exists():
            lock_path.unlink()
    except OSError:
        logger.warning("Failed to remove auto-upgrade failure lockfile")


def _record_auto_upgrade_failure(base_dir: Path, reason: str) -> int:
    count = _read_auto_upgrade_failure_count(base_dir) + 1
    _write_auto_upgrade_failure_count(base_dir, count)
    append_auto_upgrade_log(
        base_dir,
        f"Auto-upgrade failure {count}: {reason}",
    )
    return count

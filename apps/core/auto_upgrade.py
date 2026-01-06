"""Helpers for managing the auto-upgrade scheduler."""

from __future__ import annotations

import logging
from os import environ
from pathlib import Path

from django.conf import settings
from django.utils import timezone


AUTO_UPGRADE_LOG_NAME = "auto-upgrade.log"
AUTO_UPGRADE_TASK_NAME = "auto-upgrade-check"
AUTO_UPGRADE_TASK_PATH = "apps.core.tasks.check_github_updates"
AUTO_UPGRADE_FAST_LANE_LOCK_NAME = "auto_upgrade_fast_lane.lck"
AUTO_UPGRADE_FAST_LANE_INTERVAL_MINUTES = 60

DEFAULT_AUTO_UPGRADE_MODE = "stable"
AUTO_UPGRADE_CADENCE_HOUR = 4
AUTO_UPGRADE_INTERVAL_MINUTES = {
    "latest": 1440,
    "unstable": 15,
    "stable": 10080,
    "regular": 10080,
    "normal": 10080,
}
AUTO_UPGRADE_FALLBACK_INTERVAL = AUTO_UPGRADE_INTERVAL_MINUTES[DEFAULT_AUTO_UPGRADE_MODE]
AUTO_UPGRADE_CRONTAB_SCHEDULES = {
    "latest": {
        "minute": "0",
        "hour": str(AUTO_UPGRADE_CADENCE_HOUR),
        "day_of_week": "*",
        "day_of_month": "*",
        "month_of_year": "*",
    },
    "stable": {
        "minute": "0",
        "hour": str(AUTO_UPGRADE_CADENCE_HOUR),
        "day_of_week": "4",
        "day_of_month": "*",
        "month_of_year": "*",
    },
    "regular": {
        "minute": "0",
        "hour": str(AUTO_UPGRADE_CADENCE_HOUR),
        "day_of_week": "4",
        "day_of_month": "*",
        "month_of_year": "*",
    },
    "normal": {
        "minute": "0",
        "hour": str(AUTO_UPGRADE_CADENCE_HOUR),
        "day_of_week": "4",
        "day_of_month": "*",
        "month_of_year": "*",
    },
}


logger = logging.getLogger(__name__)


def auto_upgrade_log_file(base_dir: Path) -> Path:
    """Return the auto-upgrade log path, creating parent directories."""

    log_dir = Path(base_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / AUTO_UPGRADE_LOG_NAME


def append_auto_upgrade_log(base_dir: Path, message: str) -> Path:
    """Append a timestamped message to the auto-upgrade log."""

    log_file = auto_upgrade_log_file(base_dir)
    timestamp = timezone.now().isoformat()
    try:
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} {message}\n")
    except Exception:  # pragma: no cover - best effort logging only
        logger.warning("Failed to append auto-upgrade log entry: %s", message)

    return log_file


def auto_upgrade_base_dir() -> Path:
    """Return the runtime base directory for auto-upgrade state."""

    env_base_dir = environ.get("ARTHEXIS_BASE_DIR")
    if env_base_dir:
        return Path(env_base_dir)

    base_dir = getattr(settings, "BASE_DIR", None)
    if base_dir:
        if isinstance(base_dir, Path):
            return base_dir
        return Path(str(base_dir))

    return Path(__file__).resolve().parent.parent


def auto_upgrade_fast_lane_lock_file(base_dir: Path) -> Path:
    """Return the fast-lane control lock file path."""

    return Path(base_dir) / ".locks" / AUTO_UPGRADE_FAST_LANE_LOCK_NAME


def auto_upgrade_fast_lane_enabled(base_dir: Path | None = None) -> bool:
    """Return ``True`` when the fast-lane lock file exists."""

    base = Path(base_dir) if base_dir is not None else auto_upgrade_base_dir()
    lock_file = auto_upgrade_fast_lane_lock_file(base)
    try:
        return lock_file.exists()
    except OSError:  # pragma: no cover - defensive fallback
        return False


def set_auto_upgrade_fast_lane(enabled: bool, base_dir: Path | None = None) -> bool:
    """Enable or disable fast-lane scheduling via the lock file."""

    base = Path(base_dir) if base_dir is not None else auto_upgrade_base_dir()
    lock_file = auto_upgrade_fast_lane_lock_file(base)

    try:
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        if enabled:
            lock_file.touch(exist_ok=True)
        else:
            lock_file.unlink(missing_ok=True)
    except OSError:
        logger.exception("Unable to update fast-lane lock file")
        return False

    return True


def ensure_auto_upgrade_periodic_task(
    sender=None, *, base_dir: Path | None = None, **kwargs
) -> None:
    """Ensure the auto-upgrade periodic task exists.

    The function is signal-safe so it can be wired to Django's
    ``post_migrate`` hook. When called directly the ``sender`` and
    ``**kwargs`` parameters are ignored.
    """

    del sender, kwargs  # Unused when invoked as a Django signal handler.

    if base_dir is None:
        base_dir = Path(settings.BASE_DIR)
    else:
        base_dir = Path(base_dir)

    lock_dir = base_dir / ".locks"
    mode_file = lock_dir / "auto_upgrade.lck"

    try:  # pragma: no cover - optional dependency failures
        from django_celery_beat.models import (
            CrontabSchedule,
            IntervalSchedule,
            PeriodicTask,
        )
        from django.db.utils import OperationalError, ProgrammingError
    except Exception:
        return

    if not mode_file.exists():
        try:
            PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).delete()
        except (OperationalError, ProgrammingError):  # pragma: no cover - DB not ready
            return
        return

    override_interval = environ.get("ARTHEXIS_UPGRADE_FREQ")
    fast_lane_enabled = auto_upgrade_fast_lane_enabled(base_dir)

    _mode = mode_file.read_text().strip().lower() or DEFAULT_AUTO_UPGRADE_MODE
    if _mode == "version":
        _mode = DEFAULT_AUTO_UPGRADE_MODE
    interval_minutes = AUTO_UPGRADE_INTERVAL_MINUTES.get(
        _mode, AUTO_UPGRADE_FALLBACK_INTERVAL
    )

    if fast_lane_enabled:
        interval_minutes = AUTO_UPGRADE_FAST_LANE_INTERVAL_MINUTES
        override_interval = None
    elif override_interval:
        try:
            parsed_interval = int(override_interval)
        except ValueError:
            parsed_interval = None
        else:
            if parsed_interval > 0:
                interval_minutes = parsed_interval

    try:
        description = "Auto-upgrade checks run every %s minutes." % interval_minutes
        if fast_lane_enabled:
            description = "Fast Lane enabled: upgrade checks run hourly."
        if fast_lane_enabled or override_interval or _mode not in AUTO_UPGRADE_CRONTAB_SCHEDULES:
            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=interval_minutes, period=IntervalSchedule.MINUTES
            )
            defaults = {
                "interval": schedule,
                "crontab": None,
                "solar": None,
                "clocked": None,
                "task": AUTO_UPGRADE_TASK_PATH,
                "description": description,
            }
        else:
            crontab_config = AUTO_UPGRADE_CRONTAB_SCHEDULES[_mode]
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute=crontab_config["minute"],
                hour=crontab_config["hour"],
                day_of_week=crontab_config["day_of_week"],
                day_of_month=crontab_config["day_of_month"],
                month_of_year=crontab_config["month_of_year"],
                timezone=timezone.get_current_timezone_name(),
            )
            defaults = {
                "interval": None,
                "crontab": schedule,
                "solar": None,
                "clocked": None,
                "task": AUTO_UPGRADE_TASK_PATH,
                "description": description,
            }
        PeriodicTask.objects.update_or_create(
            name=AUTO_UPGRADE_TASK_NAME,
            defaults=defaults,
        )
    except (OperationalError, ProgrammingError):  # pragma: no cover - DB not ready
        return

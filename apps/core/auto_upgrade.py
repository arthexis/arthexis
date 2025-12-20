"""Helpers for managing the auto-upgrade scheduler."""

from __future__ import annotations

from os import environ
from pathlib import Path

from django.conf import settings
from django.utils import timezone


AUTO_UPGRADE_TASK_NAME = "auto-upgrade-check"
AUTO_UPGRADE_TASK_PATH = "apps.core.tasks.check_github_updates"

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

    _mode = mode_file.read_text().strip().lower() or DEFAULT_AUTO_UPGRADE_MODE
    if _mode == "version":
        _mode = DEFAULT_AUTO_UPGRADE_MODE
    interval_minutes = AUTO_UPGRADE_INTERVAL_MINUTES.get(
        _mode, AUTO_UPGRADE_FALLBACK_INTERVAL
    )

    if override_interval:
        try:
            parsed_interval = int(override_interval)
        except ValueError:
            parsed_interval = None
        else:
            if parsed_interval > 0:
                interval_minutes = parsed_interval

    try:
        if override_interval or _mode not in AUTO_UPGRADE_CRONTAB_SCHEDULES:
            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=interval_minutes, period=IntervalSchedule.MINUTES
            )
            defaults = {
                "interval": schedule,
                "crontab": None,
                "solar": None,
                "clocked": None,
                "task": AUTO_UPGRADE_TASK_PATH,
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
            }
        PeriodicTask.objects.update_or_create(
            name=AUTO_UPGRADE_TASK_NAME,
            defaults=defaults,
        )
    except (OperationalError, ProgrammingError):  # pragma: no cover - DB not ready
        return

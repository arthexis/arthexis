"""Helpers for managing the auto-upgrade scheduler."""

from __future__ import annotations

from os import environ
from pathlib import Path

from django.conf import settings


AUTO_UPGRADE_TASK_NAME = "auto-upgrade-check"
AUTO_UPGRADE_TASK_PATH = "apps.core.tasks.check_github_updates"

DEFAULT_AUTO_UPGRADE_MODE = "stable"
AUTO_UPGRADE_INTERVAL_MINUTES = {
    "latest": 60,
    "unstable": 15,
    "stable": 1440,
    "regular": 1440,
    "normal": 1440,
}
AUTO_UPGRADE_FALLBACK_INTERVAL = AUTO_UPGRADE_INTERVAL_MINUTES[DEFAULT_AUTO_UPGRADE_MODE]


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

    lock_dir = base_dir / "locks"
    mode_file = lock_dir / "auto_upgrade.lck"

    try:  # pragma: no cover - optional dependency failures
        from django_celery_beat.models import IntervalSchedule, PeriodicTask
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
        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=interval_minutes, period=IntervalSchedule.MINUTES
        )
        PeriodicTask.objects.update_or_create(
            name=AUTO_UPGRADE_TASK_NAME,
            defaults={
                "interval": schedule,
                "task": AUTO_UPGRADE_TASK_PATH,
            },
        )
    except (OperationalError, ProgrammingError):  # pragma: no cover - DB not ready
        return

"""Helpers for managing the auto-upgrade scheduler."""

from __future__ import annotations

import logging
import re
from os import environ
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps.core.versioning import (
    AUTO_UPGRADE_DAY_MINUTES,
    AUTO_UPGRADE_WEEK_MINUTES,
)

AUTO_UPGRADE_LOG_NAME = "auto-upgrade.log"
AUTO_UPGRADE_TASK_NAME = "auto-upgrade-check"
AUTO_UPGRADE_TASK_PATH = "apps.nodes.tasks.apply_upgrade_policies"
AUTO_UPGRADE_FEATURE_SLUG = "auto-upgrade"
AUTO_UPGRADE_FAST_LANE_LOCK_NAME = "auto_upgrade_fast_lane.lck"
AUTO_UPGRADE_FAST_LANE_INTERVAL_MINUTES = 60

DEFAULT_AUTO_UPGRADE_MODE = "stable"
AUTO_UPGRADE_CADENCE_HOUR = 4
AUTO_UPGRADE_INTERVAL_MINUTES = {
    "latest": AUTO_UPGRADE_DAY_MINUTES,
    "unstable": AUTO_UPGRADE_DAY_MINUTES,
    "stable": AUTO_UPGRADE_WEEK_MINUTES,
    "lts": AUTO_UPGRADE_WEEK_MINUTES,
    "regular": AUTO_UPGRADE_DAY_MINUTES,
    "normal": AUTO_UPGRADE_DAY_MINUTES,
    "version": AUTO_UPGRADE_DAY_MINUTES,
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
    "unstable": {
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
    "lts": {
        "minute": "0",
        "hour": str(AUTO_UPGRADE_CADENCE_HOUR),
        "day_of_week": "4",
        "day_of_month": "*",
        "month_of_year": "*",
    },
    "regular": {
        "minute": "0",
        "hour": str(AUTO_UPGRADE_CADENCE_HOUR),
        "day_of_week": "*",
        "day_of_month": "*",
        "month_of_year": "*",
    },
    "normal": {
        "minute": "0",
        "hour": str(AUTO_UPGRADE_CADENCE_HOUR),
        "day_of_week": "*",
        "day_of_month": "*",
        "month_of_year": "*",
    },
    "version": {
        "minute": "0",
        "hour": str(AUTO_UPGRADE_CADENCE_HOUR),
        "day_of_week": "*",
        "day_of_month": "*",
        "month_of_year": "*",
    },
}


logger = logging.getLogger(__name__)

AUTO_UPGRADE_FAILURE_GUIDE = (
    {
        "code": "NET-FAIL",
        "label": "Network unavailable",
        "details": "The upgrade check could not reach upstream services.",
        "action": "Check DNS, gateway, firewall, and proxy settings, then rerun Pre-Upgrade Checks.",
    },
    {
        "code": "URL-ERR",
        "label": "Local URL error",
        "details": "The post-upgrade health check could not open the local service URL.",
        "action": "Verify the suite services are running and review nginx or gunicorn logs.",
    },
    {
        "code": "HTTP-###",
        "label": "HTTP error",
        "details": "The health check reached the suite but received a non-200 status.",
        "action": "Inspect application logs and confirm dependencies are healthy before retrying.",
    },
    {
        "code": "CI-FAIL",
        "label": "CI failing",
        "details": "The candidate revision reported failing CI status.",
        "action": "Wait for CI to pass or pick a different release revision.",
    },
    {
        "code": "GIT-ERR",
        "label": "Git fetch error",
        "details": "Fetching the upstream repository failed.",
        "action": "Confirm git remote access, credentials, and disk space.",
    },
    {
        "code": "UPG-SCRIPT",
        "label": "Upgrade script error",
        "details": "The upgrade script exited with an error.",
        "action": "Review upgrade logs and re-run the script manually if needed.",
    },
    {
        "code": "UPG-LAUNCH",
        "label": "Upgrade launch failed",
        "details": "The upgrade command could not be started or delegated.",
        "action": "Verify script permissions and the systemd service configuration.",
    },
    {
        "code": "HLTH-FAIL",
        "label": "Health check failed",
        "details": "Post-upgrade health checks did not succeed.",
        "action": "Check service logs, then rerun after resolving the failure.",
    },
    {
        "code": "PROC-ERR",
        "label": "Subprocess error",
        "details": "An unexpected subprocess failure occurred during upgrade checks.",
        "action": "Review the auto-upgrade log for the failing command.",
    },
    {
        "code": "TIMEOUT",
        "label": "Timeout",
        "details": "A request timed out during the upgrade workflow.",
        "action": "Verify service responsiveness and network stability.",
    },
    {
        "code": "AUTO-FAIL",
        "label": "Unknown failure",
        "details": "The error could not be classified into a known category.",
        "action": "Review the auto-upgrade log for the full error output.",
    },
)


def auto_upgrade_failure_guide() -> list[dict[str, str]]:
    """Return the failure guide entries used by the upgrade report view."""

    return list(AUTO_UPGRADE_FAILURE_GUIDE)


def shorten_auto_upgrade_failure(reason: str) -> str:
    """Return a shortened failure code suitable for LCD output."""

    tokens = re.findall(r"[A-Z0-9]+", reason.upper())
    if not tokens:
        return "AUTO-FAIL"

    if "HTTP" in tokens:
        http_index = tokens.index("HTTP")
        if http_index + 1 < len(tokens) and tokens[http_index + 1].isdigit():
            return f"HTTP-{tokens[http_index + 1]}"[:16]
        return "HTTP-ERR"

    if "URLOPEN" in tokens or "URLERROR" in tokens or "URL" in tokens:
        if "ERRNO" in tokens:
            err_index = tokens.index("ERRNO")
            if err_index + 1 < len(tokens) and tokens[err_index + 1].isdigit():
                return f"URL-ERR{tokens[err_index + 1]}"[:16]
        return "URL-ERR"

    if "NETWORK" in tokens or "UNREACHABLE" in tokens:
        return "NET-FAIL"

    if "CI" in tokens:
        return "CI-FAIL"

    if "GIT" in tokens:
        return "GIT-ERR"

    if "UPGRADE" in tokens and "LAUNCH" in tokens:
        return "UPG-LAUNCH"

    if "UPGRADE" in tokens and "SCRIPT" in tokens:
        return "UPG-SCRIPT"

    if "HEALTH" in tokens:
        return "HLTH-FAIL"

    if "SUBPROCESS" in tokens:
        return "PROC-ERR"

    if "TIMEOUT" in tokens:
        return "TIMEOUT"

    short_code = "-".join(tokens[:3])
    return short_code[:16]


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


def _resolve_policy_interval_minutes() -> int:
    """Resolve the active auto-upgrade interval in minutes.

    Returns:
        int: The smallest active policy interval, clamped to at least one minute.
    """

    try:  # pragma: no cover - optional dependency failures
        from django.db import DatabaseError

        from apps.nodes.models import Node, UpgradePolicy
    except Exception:
        return AUTO_UPGRADE_INTERVAL_MINUTES.get("unstable", AUTO_UPGRADE_FAST_LANE_INTERVAL_MINUTES)

    try:
        local = Node.get_local()
    except DatabaseError:
        local = None

    try:
        if local:
            policies = list(local.upgrade_policies.filter(is_active=True))
        else:
            policies = list(UpgradePolicy.objects.filter(is_active=True))
    except DatabaseError:
        return AUTO_UPGRADE_INTERVAL_MINUTES.get("unstable", AUTO_UPGRADE_FAST_LANE_INTERVAL_MINUTES)

    if not policies:
        return AUTO_UPGRADE_INTERVAL_MINUTES.get("unstable", AUTO_UPGRADE_FAST_LANE_INTERVAL_MINUTES)

    intervals = [policy.interval_minutes for policy in policies if policy.interval_minutes]
    if not intervals:
        return AUTO_UPGRADE_INTERVAL_MINUTES.get("unstable", AUTO_UPGRADE_FAST_LANE_INTERVAL_MINUTES)
    return max(1, min(intervals))


def _get_or_create_interval_schedule(*, every: int, period: str):
    """Return a unique beat interval schedule for the provided cadence.

    Parameters:
        every: The numeric interval amount to match.
        period: The django-celery-beat period constant to match.

    Returns:
        IntervalSchedule: An existing schedule when one already matches, otherwise
        a newly created schedule.

    Raises:
        OperationalError: Propagated when the database is not ready.
        ProgrammingError: Propagated when the beat tables are not available yet.
    """

    from django_celery_beat.models import IntervalSchedule

    queryset = IntervalSchedule.objects.filter(every=every, period=period).order_by("pk")
    schedule = queryset.first()
    if schedule is not None:
        return schedule
    return IntervalSchedule.objects.create(every=every, period=period)


def auto_upgrade_suite_feature_enabled(*, default: bool = True) -> bool:
    """Return whether the auto-upgrade suite feature is enabled."""

    try:
        from apps.features.utils import is_suite_feature_enabled
    except ImportError:
        return default

    return is_suite_feature_enabled(AUTO_UPGRADE_FEATURE_SLUG, default=default)


def sync_auto_upgrade_periodic_task_for_feature_change(
    *, instance=None, update_fields=None, **kwargs
) -> None:
    """Sync beat task state when the auto-upgrade suite feature toggles."""

    del kwargs
    if instance is None:
        return
    if getattr(instance, "slug", None) != AUTO_UPGRADE_FEATURE_SLUG:
        return
    if update_fields is not None and "is_enabled" not in set(update_fields):
        return
    ensure_auto_upgrade_periodic_task()


def ensure_auto_upgrade_periodic_task(
    sender=None, *, base_dir: Path | None = None, **kwargs
) -> None:
    """Ensure the auto-upgrade periodic task exists.

    The function is signal-safe so it can be wired to Django's
    ``post_migrate`` hook. When called directly the ``sender`` and
    ``**kwargs`` parameters are ignored.
    """

    del sender, kwargs, base_dir  # Unused when invoked as a Django signal handler.

    try:  # pragma: no cover - optional dependency failures
        from django.db.utils import OperationalError, ProgrammingError
        from django_celery_beat.models import (
            IntervalSchedule,
            PeriodicTask,
        )
    except Exception:
        return

    override_interval = environ.get("ARTHEXIS_UPGRADE_FREQ")
    interval_minutes = _resolve_policy_interval_minutes()
    if override_interval:
        try:
            parsed_interval = int(override_interval)
        except ValueError:
            parsed_interval = None
        else:
            if parsed_interval and parsed_interval > 0:
                # Global override: applies to all policies (including fast lane).
                interval_minutes = parsed_interval

    try:
        description = f"Upgrade policy checks run every {interval_minutes} minutes."
        schedule = _get_or_create_interval_schedule(
            every=interval_minutes,
            period=IntervalSchedule.MINUTES,
        )
        feature_enabled = auto_upgrade_suite_feature_enabled(default=True)
        defaults = {
            "interval": schedule,
            "crontab": None,
            "solar": None,
            "clocked": None,
            "task": AUTO_UPGRADE_TASK_PATH,
            "description": description,
            "enabled": feature_enabled,
        }
        PeriodicTask.objects.update_or_create(
            name=AUTO_UPGRADE_TASK_NAME,
            defaults=defaults,
        )
    except (OperationalError, ProgrammingError):  # pragma: no cover - DB not ready
        return

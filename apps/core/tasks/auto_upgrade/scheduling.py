from __future__ import annotations

import logging
import os
from datetime import time as datetime_time

from django.utils import timezone

from apps.core.auto_upgrade import (
    AUTO_UPGRADE_FALLBACK_INTERVAL,
    AUTO_UPGRADE_FAST_LANE_INTERVAL_MINUTES,
    AUTO_UPGRADE_INTERVAL_MINUTES,
    auto_upgrade_base_dir,
    auto_upgrade_fast_lane_enabled,
)

from .locks import _auto_upgrade_ran_recently


logger = logging.getLogger(__name__)

STABLE_AUTO_UPGRADE_START = datetime_time(hour=19, minute=30)
STABLE_AUTO_UPGRADE_END = datetime_time(hour=5, minute=30)


def _resolve_auto_upgrade_interval_minutes(mode: str) -> int:
    base_dir = auto_upgrade_base_dir()
    if auto_upgrade_fast_lane_enabled(base_dir):
        return AUTO_UPGRADE_FAST_LANE_INTERVAL_MINUTES

    interval_minutes = AUTO_UPGRADE_INTERVAL_MINUTES.get(
        mode, AUTO_UPGRADE_FALLBACK_INTERVAL
    )

    override_interval = os.environ.get("ARTHEXIS_UPGRADE_FREQ")
    if override_interval:
        try:
            parsed_interval = int(override_interval)
        except ValueError:
            parsed_interval = None
        else:
            if parsed_interval > 0:
                interval_minutes = parsed_interval

    return interval_minutes


def _is_within_stable_upgrade_window(current: timezone.datetime | None = None) -> bool:
    """Return whether the current time is inside the stable upgrade window."""

    if current is None:
        current = timezone.localtime(timezone.now())
    else:
        current = timezone.localtime(current)

    current_time = current.time()
    return (
        current_time >= STABLE_AUTO_UPGRADE_START
        or current_time <= STABLE_AUTO_UPGRADE_END
    )


def _apply_stable_schedule_guard(
    base_dir, mode, ops, log_appender
) -> bool:
    if mode.mode != "stable" or mode.admin_override:
        return True

    now_local = timezone.localtime(timezone.now())
    if _is_within_stable_upgrade_window(now_local):
        return True

    log_appender(
        base_dir,
        "Skipping stable auto-upgrade; outside the 7:30 PM to 5:30 AM window",
    )
    ops.ensure_runtime_services(
        base_dir,
        restart_if_active=False,
        revert_on_failure=False,
        log_appender=log_appender,
    )
    return False


def _recent_auto_upgrade_skip(
    base_dir,
    mode,
    log_appender,
    ops,
) -> bool:
    if mode.admin_override or mode.skip_recency_check:
        return False
    if not _auto_upgrade_ran_recently(base_dir, mode.interval_minutes):
        return False
    log_appender(
        base_dir,
        (
            "Skipping auto-upgrade; last run was less than "
            f"{mode.interval_minutes} minutes ago"
        ),
    )
    ops.ensure_runtime_services(
        base_dir,
        restart_if_active=False,
        revert_on_failure=False,
        log_appender=log_appender,
    )
    return True

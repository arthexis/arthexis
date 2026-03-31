"""Uptime and shutdown history helpers for system UI reporting.

Data flow:
- ``last -x -F`` output is parsed into shutdown intervals.
- Suite lock metadata and host boot time are translated into an additional
  offline interval when appropriate.
- Intervals are merged and projected into fixed windows (24h/7d/30d), then
  serialized into timeline segments and summary percentages.

Expected parsed log formats:
- ``last -x -F`` lines that begin with ``shutdown`` or ``reboot`` and contain
  timestamps like ``Mon Jan 02 15:04:05 2024``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from datetime import datetime, timedelta
from datetime import timezone as datetime_timezone

from django.utils import timezone
from django.utils.timesince import timesince
from django.utils.translation import gettext_lazy as _

from ..filesystem import _suite_uptime_lock_info
from .formatting import _as_str, _format_datetime
from .services import (
    SerializedUptimeSegmentPayload,
    SuiteUptimeDetailsPayload,
    UptimeReportPayload,
    UptimeReportSuitePayload,
    UptimeSegmentPayload,
    UptimeWindowPayload,
)

_DAY_NAMES = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}


def _system_boot_time(now: datetime | None = None) -> datetime | None:
    """Return the host boot time if it can be determined."""

    current_time = now or timezone.now()
    try:
        import psutil
    except Exception:
        return None

    try:
        boot_timestamp = float(psutil.boot_time())
    except Exception:
        return None

    if not boot_timestamp:
        return None

    boot_time = datetime.fromtimestamp(boot_timestamp, tz=datetime_timezone.utc)
    if boot_time > current_time:
        return None

    return boot_time


def _suite_uptime_details() -> SuiteUptimeDetailsPayload:
    """Return structured uptime information for the running suite if possible."""

    now = timezone.now()
    lock_info = _suite_uptime_lock_info(now=now)
    boot_time = _system_boot_time(now)
    lock_start = lock_info.get("started_at")
    lock_started_at = lock_start if isinstance(lock_start, datetime) else None

    if lock_started_at and boot_time and lock_started_at < boot_time:
        return {
            "available": False,
            "boot_time": boot_time,
            "boot_time_label": _format_datetime(boot_time),
            "lock_started_at": lock_started_at,
        }

    if lock_info.get("fresh") and lock_started_at:
        uptime_label = timesince(lock_started_at, now)
        return {
            "uptime": uptime_label,
            "boot_time": lock_started_at,
            "boot_time_label": _format_datetime(lock_started_at),
            "available": True,
        }

    if lock_info.get("exists"):
        if boot_time:
            uptime_label = timesince(boot_time, now)
            details: SuiteUptimeDetailsPayload = {
                "uptime": uptime_label,
                "boot_time": boot_time,
                "boot_time_label": _format_datetime(boot_time),
                "available": True,
            }
            if lock_started_at:
                details["lock_started_at"] = lock_started_at
            return details
        return {
            "available": False,
            "boot_time": boot_time,
            "boot_time_label": "",
        }

    if boot_time:
        uptime_label = timesince(boot_time, now)
        return {
            "uptime": uptime_label,
            "boot_time": boot_time,
            "boot_time_label": _format_datetime(boot_time),
            "available": True,
        }

    return {
        "available": False,
        "boot_time": None,
        "boot_time_label": "",
    }


def _suite_uptime() -> str:
    """Return a human-readable uptime for the running suite when possible."""

    return _suite_uptime_details().get("uptime", "")


def _suite_offline_period(now: datetime) -> tuple[datetime, datetime] | None:
    """Return a downtime window when the lock predates the current boot."""

    lock_info = _suite_uptime_lock_info(now=now)
    lock_start = lock_info.get("started_at")
    boot_time = _system_boot_time(now)

    if boot_time and isinstance(lock_start, datetime) and lock_start < boot_time:
        return boot_time, now

    return None


def suite_offline_period(now: datetime) -> tuple[datetime, datetime] | None:
    """Return a downtime window when the lock predates the current boot."""

    return _suite_offline_period(now)


def _parse_last_history_line(line: str) -> dict[str, datetime | str | None] | None:
    """Parse one ``last -x -F`` line into record metadata."""

    if not line or line.startswith("wtmp begins"):
        return None

    tokens = line.split()
    if not tokens or tokens[0] not in {"reboot", "shutdown"}:
        return None

    try:
        start_index = next(index for index, token in enumerate(tokens) if token in _DAY_NAMES)
    except StopIteration:
        return None

    if start_index + 4 >= len(tokens):
        return None

    start_text = " ".join(tokens[start_index : start_index + 5])
    try:
        start_dt = datetime.strptime(start_text, "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return None
    start_dt = timezone.make_aware(start_dt, timezone.get_current_timezone())

    dash_index = None
    for index in range(start_index + 5, len(tokens)):
        if tokens[index] == "-":
            dash_index = index
            break

    end_dt = None
    if dash_index is not None and dash_index + 5 < len(tokens):
        end_text = " ".join(tokens[dash_index + 1 : dash_index + 6])
        try:
            end_dt = datetime.strptime(end_text, "%a %b %d %H:%M:%S %Y")
        except ValueError:
            end_dt = None
        else:
            end_dt = timezone.make_aware(end_dt, timezone.get_current_timezone())

    return {"type": tokens[0], "start": start_dt, "end": end_dt}


def _load_shutdown_periods() -> tuple[list[tuple[datetime, datetime | None]], str | None]:
    """Return shutdown periods parsed from ``last -x -F`` output."""

    try:
        result = subprocess.run(
            ["last", "-x", "-F"],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except FileNotFoundError:
        return [], _as_str(_("The `last` command is not available on this node."))
    except subprocess.TimeoutExpired:
        return [], _as_str(_("Timed out while reading uptime history from the system log."))

    if result.returncode not in (0, 1):
        return [], _as_str(_("Unable to read uptime history from the system log."))

    shutdown_periods: list[tuple[datetime, datetime | None]] = []
    for line in result.stdout.splitlines():
        record = _parse_last_history_line(line.strip())
        if not record or record["type"] != "shutdown":
            continue
        start = record.get("start")
        end = record.get("end")
        if isinstance(start, datetime):
            shutdown_periods.append((start, end if isinstance(end, datetime) else None))

    return shutdown_periods, None


def load_shutdown_periods() -> tuple[list[tuple[datetime, datetime | None]], str | None]:
    """Return shutdown periods parsed from ``last -x -F`` output."""

    return _load_shutdown_periods()


def _merge_shutdown_periods(periods: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    normalized: list[tuple[datetime, datetime]] = []
    for start, end in periods:
        if end < start:
            continue
        normalized.append((start, end))

    normalized.sort(key=lambda value: value[0])
    merged: list[tuple[datetime, datetime]] = []
    for start, end in normalized:
        if not merged:
            merged.append((start, end))
            continue
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _build_uptime_segments(
    *, window_start: datetime, window_end: datetime, shutdown_periods: list[tuple[datetime, datetime]]
) -> list[UptimeSegmentPayload]:
    """Build alternating up/down segments across a reporting window."""

    segments: list[UptimeSegmentPayload] = []
    if window_end <= window_start:
        return segments

    merged_periods = _merge_shutdown_periods(shutdown_periods)
    cursor = window_start
    for down_start, down_end in merged_periods:
        if down_end <= window_start or down_start >= window_end:
            continue
        if cursor < down_start:
            up_end = min(down_start, window_end)
            duration = up_end - cursor
            segments.append(
                {
                    "status": "up",
                    "start": cursor,
                    "end": up_end,
                    "duration": duration,
                }
            )
        segment_start = max(down_start, window_start)
        segment_end = min(down_end, window_end)
        duration = segment_end - segment_start
        segments.append(
            {
                "status": "down",
                "start": segment_start,
                "end": segment_end,
                "duration": duration,
            }
        )
        cursor = segment_end
    if cursor < window_end:
        duration = window_end - cursor
        segments.append(
            {
                "status": "up",
                "start": cursor,
                "end": window_end,
                "duration": duration,
            }
        )

    return segments


def build_uptime_segments(
    *, window_start: datetime, window_end: datetime, shutdown_periods: list[tuple[datetime, datetime]]
) -> list[UptimeSegmentPayload]:
    """Public wrapper for uptime segment generation."""

    return _build_uptime_segments(
        window_start=window_start,
        window_end=window_end,
        shutdown_periods=shutdown_periods,
    )


def _serialize_segments(
    segments: list[UptimeSegmentPayload], *, window_duration: float
) -> list[SerializedUptimeSegmentPayload]:
    serialized: list[SerializedUptimeSegmentPayload] = []
    for segment in segments:
        start = segment["start"]
        end = segment["end"]
        duration: timedelta = segment["duration"]
        duration_seconds = max(duration.total_seconds(), 0.0)
        width = 0.0
        if window_duration > 0:
            width = (duration_seconds / window_duration) * 100
        serialized.append(
            {
                "status": segment["status"],
                "start": start,
                "end": end,
                "width": width,
                "duration": duration,
                "duration_label": timesince(start, end),
                "label": _("%(status)s from %(start)s to %(end)s")
                % {
                    "status": _(segment["status"] == "up" and "Up" or "Down"),
                    "start": _format_datetime(start),
                    "end": _format_datetime(end),
                },
            }
        )
    return serialized


def _build_uptime_report(*, now: datetime | None = None) -> UptimeReportPayload:
    """Build uptime report windows and suite uptime summary."""

    current_time = now or timezone.now()
    raw_periods, error = _load_shutdown_periods()
    shutdown_periods = []
    for start, end in raw_periods:
        normalized_end = end or current_time
        if normalized_end < start:
            continue
        shutdown_periods.append((start, normalized_end))

    offline_period = _suite_offline_period(current_time)
    if offline_period:
        shutdown_periods.append(offline_period)

    windows = [
        (_as_str(_("Last 24 hours")), current_time - timedelta(hours=24)),
        (_as_str(_("Last 7 days")), current_time - timedelta(days=7)),
        (_as_str(_("Last 30 days")), current_time - timedelta(days=30)),
    ]

    report_windows: list[UptimeWindowPayload] = []
    for label, start in windows:
        window_duration = (current_time - start).total_seconds()
        segments = _build_uptime_segments(
            window_start=start, window_end=current_time, shutdown_periods=shutdown_periods
        )
        serialized_segments = _serialize_segments(segments, window_duration=window_duration)
        uptime_seconds = sum(
            segment["duration"].total_seconds()
            for segment in serialized_segments
            if segment["status"] == "up"
        )
        downtime_seconds = max(window_duration - uptime_seconds, 0.0)
        uptime_percent = 0.0
        downtime_percent = 0.0
        if window_duration > 0:
            uptime_percent = (uptime_seconds / window_duration) * 100
            downtime_percent = (downtime_seconds / window_duration) * 100

        report_windows.append(
            {
                "label": label,
                "start": start,
                "end": current_time,
                "segments": serialized_segments,
                "uptime_percent": round(uptime_percent, 1),
                "downtime_percent": round(downtime_percent, 1),
                "downtime_events": [
                    {
                        "start": _format_datetime(segment["start"]),
                        "end": _format_datetime(segment["end"]),
                        "duration": timesince(segment["start"], segment["end"]),
                    }
                    for segment in serialized_segments
                    if segment["status"] == "down"
                ],
            }
        )

    suite_details = _suite_uptime_details()
    suite_info: UptimeReportSuitePayload = {
        "uptime": suite_details.get("uptime", ""),
        "boot_time": suite_details.get("boot_time"),
        "boot_time_label": suite_details.get("boot_time_label", ""),
        "available": bool(suite_details.get("available") or suite_details.get("uptime")),
    }

    return {
        "generated_at": current_time,
        "windows": report_windows,
        "error": error,
        "suite": suite_info,
    }

"""Utilities for generating Celery-focused admin reports."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
import numbers
import re
from pathlib import Path
from typing import Iterable, Iterator

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class ReportPeriod:
    """Representation of an available reporting window."""

    key: str
    label: str
    delta: timedelta


REPORT_PERIOD_ORDER = ("1d", "7d", "30d")
REPORT_PERIODS: dict[str, ReportPeriod] = {
    "1d": ReportPeriod("1d", _("Single day"), timedelta(days=1)),
    "7d": ReportPeriod("7d", _("Seven days"), timedelta(days=7)),
    "30d": ReportPeriod("30d", _("Monthly"), timedelta(days=30)),
}


@dataclass(frozen=True)
class ScheduledTaskSummary:
    """Human-friendly representation of a Celery scheduled task."""

    name: str
    task: str
    schedule_type: str
    schedule_description: str
    next_run: datetime | None
    enabled: bool
    source: str


@dataclass(frozen=True)
class CeleryLogEntry:
    """A parsed log entry relevant to Celery activity."""

    timestamp: datetime
    level: str
    logger: str
    message: str
    source: str


@dataclass(frozen=True)
class CeleryLogCollection:
    """Container for log entries and the sources scanned."""

    entries: list[CeleryLogEntry]
    checked_sources: list[str]


_LOG_LINE_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d{1,6})?) "
    r"\[(?P<level>[A-Z]+)\] (?P<logger>[^:]+): (?P<message>.*)$"
)


def iter_report_periods() -> Iterator[ReportPeriod]:
    """Yield configured reporting periods in display order."""

    for key in REPORT_PERIOD_ORDER:
        period = REPORT_PERIODS[key]
        yield period


def resolve_period(period_key: str | None) -> ReportPeriod:
    """Return the requested reporting period or fall back to the default."""

    if not period_key:
        return REPORT_PERIODS[REPORT_PERIOD_ORDER[0]]
    return REPORT_PERIODS.get(period_key, REPORT_PERIODS[REPORT_PERIOD_ORDER[0]])


def collect_scheduled_tasks(now: datetime, window_end: datetime) -> list[ScheduledTaskSummary]:
    """Return Celery tasks scheduled to run before ``window_end``.

    Tasks with unknown scheduling information are included to avoid omitting
    potentially important configuration.
    """

    summaries: list[ScheduledTaskSummary] = []
    summaries.extend(_collect_db_tasks(now))
    summaries.extend(_collect_settings_tasks(now))

    filtered: list[ScheduledTaskSummary] = []
    for summary in summaries:
        if summary.next_run is None or summary.next_run <= window_end:
            filtered.append(summary)

    far_future = datetime.max.replace(tzinfo=dt_timezone.utc)
    filtered.sort(
        key=lambda item: (
            item.next_run or far_future,
            item.name.lower(),
        )
    )
    return filtered


def collect_celery_log_entries(
    start: datetime, end: datetime, *, max_lines: int = 500
) -> CeleryLogCollection:
    """Return Celery-related log entries within ``start`` and ``end``."""

    entries: list[CeleryLogEntry] = []
    checked_sources: list[str] = []

    for path in _candidate_log_files():
        checked_sources.append(path.name)
        for entry in _read_log_entries(path, max_lines=max_lines):
            if entry.timestamp < start or entry.timestamp > end:
                continue
            entries.append(entry)

    entries.sort(key=lambda item: item.timestamp, reverse=True)
    return CeleryLogCollection(entries=entries, checked_sources=checked_sources)


def _collect_db_tasks(now: datetime) -> list[ScheduledTaskSummary]:
    try:  # pragma: no cover - optional dependency guard
        from django_celery_beat.models import PeriodicTask
    except Exception:
        return []

    try:
        tasks = list(
            PeriodicTask.objects.select_related(
                "interval", "crontab", "solar", "clocked"
            )
        )
    except Exception:  # pragma: no cover - database unavailable
        return []

    summaries: list[ScheduledTaskSummary] = []
    for task in tasks:
        schedule = getattr(task, "schedule", None)
        next_run = _estimate_next_run(now, schedule, task.last_run_at, task.start_time)
        schedule_type = _determine_schedule_type(task)
        schedule_description = _describe_db_schedule(task)
        summaries.append(
            ScheduledTaskSummary(
                name=task.name,
                task=task.task,
                schedule_type=schedule_type,
                schedule_description=schedule_description,
                next_run=next_run,
                enabled=bool(task.enabled),
                source=str(_("Database")),
            )
        )
    return summaries


def _collect_settings_tasks(now: datetime) -> list[ScheduledTaskSummary]:
    schedule_config = getattr(settings, "CELERY_BEAT_SCHEDULE", {})
    summaries: list[ScheduledTaskSummary] = []

    for name, config in schedule_config.items():
        task_name = str(config.get("task", ""))
        schedule = config.get("schedule")
        next_run = _estimate_next_run(now, schedule, None, None)
        schedule_type = _describe_schedule_type(schedule)
        schedule_description = _describe_settings_schedule(schedule)
        summaries.append(
            ScheduledTaskSummary(
                name=name,
                task=task_name,
                schedule_type=schedule_type,
                schedule_description=schedule_description,
                next_run=next_run,
                enabled=True,
                source=str(_("Settings")),
            )
        )

    return summaries


def _determine_schedule_type(task) -> str:
    if getattr(task, "clocked_id", None):
        return "clocked"
    if getattr(task, "solar_id", None):
        return "solar"
    if getattr(task, "crontab_id", None):
        return "crontab"
    if getattr(task, "interval_id", None):
        return "interval"
    return "unknown"


def _estimate_next_run(
    now: datetime,
    schedule,
    last_run_at: datetime | None,
    start_time: datetime | None,
) -> datetime | None:
    if schedule is None:
        return None

    if isinstance(schedule, timedelta):
        return now + schedule

    if isinstance(schedule, numbers.Real):
        return now + timedelta(seconds=float(schedule))

    if isinstance(schedule, datetime):
        candidate = _make_aware(schedule)
        if candidate and candidate >= now:
            return candidate
        return candidate

    schedule_now = _schedule_now(schedule, now)
    candidate_start = _coerce_with_schedule(schedule, start_time)
    if candidate_start and candidate_start > schedule_now:
        return candidate_start

    reference = _coerce_with_schedule(schedule, last_run_at) or schedule_now

    try:
        remaining = schedule.remaining_estimate(reference)
        if remaining is None:
            return None
        return schedule_now + remaining
    except Exception:
        try:
            due, next_time_to_run = schedule.is_due(reference)
        except Exception:
            return None
        if due:
            return schedule_now
        try:
            seconds = float(next_time_to_run)
        except (TypeError, ValueError):
            return None
        return schedule_now + timedelta(seconds=seconds)


def _schedule_now(schedule, fallback: datetime) -> datetime:
    if hasattr(schedule, "now") and hasattr(schedule, "maybe_make_aware"):
        try:
            current = schedule.maybe_make_aware(schedule.now())
            if isinstance(current, datetime):
                return current
        except Exception:
            pass
    return fallback


def _coerce_with_schedule(schedule, value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if hasattr(schedule, "maybe_make_aware"):
        try:
            coerced = schedule.maybe_make_aware(value)
            if isinstance(coerced, datetime):
                return coerced
        except Exception:
            pass
    return _make_aware(value)


def _make_aware(value: datetime) -> datetime:
    if timezone.is_naive(value):
        try:
            return timezone.make_aware(value)
        except Exception:
            return value
    return value


def _describe_db_schedule(task) -> str:
    schedule = getattr(task, "schedule", None)
    if schedule is None:
        return ""

    try:
        human_readable = getattr(schedule, "human_readable", None)
        if callable(human_readable):
            return str(human_readable())
        if isinstance(human_readable, str):
            return human_readable
    except Exception:
        pass

    for attr in ("clocked", "solar", "crontab", "interval"):
        obj = getattr(task, attr, None)
        if obj is not None:
            return str(obj)
    return str(schedule)


def _describe_schedule_type(schedule) -> str:
    type_name = type(schedule).__name__ if schedule is not None else "unknown"
    return type_name.replace("Schedule", "").lower()


def _describe_settings_schedule(schedule) -> str:
    if schedule is None:
        return ""

    try:
        human_readable = getattr(schedule, "human_readable", None)
        if callable(human_readable):
            return str(human_readable())
        if isinstance(human_readable, str):
            return human_readable
    except Exception:
        pass

    if isinstance(schedule, timedelta):
        return str(schedule)
    if isinstance(schedule, numbers.Real):
        return _("Every %(seconds)s seconds") % {"seconds": schedule}
    return str(schedule)


def _candidate_log_files() -> Iterable[Path]:
    log_dir = Path(settings.LOG_DIR)
    candidates = [
        log_dir / "celery.log",
        log_dir / "celery-worker.log",
        log_dir / "celery-beat.log",
        log_dir / getattr(settings, "LOG_FILE_NAME", ""),
    ]

    seen: set[Path] = set()
    for path in candidates:
        if not path:
            continue
        if path in seen:
            continue
        seen.add(path)
        if path.exists():
            yield path


def _read_log_entries(path: Path, *, max_lines: int) -> Iterator[CeleryLogEntry]:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            lines = deque(handle, maxlen=max_lines)
    except OSError:  # pragma: no cover - filesystem errors
        return iter(())

    return (
        entry
        for entry in (_parse_log_line(line, path.name) for line in lines)
        if entry is not None
    )


def _parse_log_line(line: str, source: str) -> CeleryLogEntry | None:
    match = _LOG_LINE_PATTERN.match(line)
    if not match:
        return None

    timestamp = _parse_timestamp(match.group("timestamp"))
    if timestamp is None:
        return None

    logger_name = match.group("logger").strip()
    message = match.group("message").strip()
    level = match.group("level").strip()

    if not _is_celery_related(logger_name, message):
        return None

    return CeleryLogEntry(
        timestamp=timestamp,
        level=level,
        logger=logger_name,
        message=message,
        source=source,
    )


def _parse_timestamp(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S,%f", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(value, fmt)
            return _make_aware(dt)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _make_aware(dt)


def _is_celery_related(logger_name: str, message: str) -> bool:
    logger_lower = logger_name.lower()
    message_lower = message.lower()
    if any(keyword in logger_lower for keyword in ("celery", "task", "beat")):
        return True
    return "celery" in message_lower or "task" in message_lower


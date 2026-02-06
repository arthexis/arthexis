from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from django.utils import timezone
from django.utils.timesince import timesince
from django.utils.translation import gettext_lazy as _

from ..filesystem import _startup_report_log_path, _startup_report_reference_time
from .formatters import _format_datetime


STARTUP_REPORT_DEFAULT_LIMIT = 50
STARTUP_CLOCK_DRIFT_THRESHOLD = timedelta(minutes=5)


def _parse_startup_report_entry(line: str) -> dict[str, object] | None:
    text = line.strip()
    if not text:
        return None

    parts = text.split("\t")
    timestamp_raw = parts[0] if parts else ""
    script = parts[1] if len(parts) > 1 else ""
    event = parts[2] if len(parts) > 2 else ""
    detail = parts[3] if len(parts) > 3 else ""

    parsed_timestamp = None
    if timestamp_raw:
        try:
            parsed_timestamp = datetime.fromisoformat(timestamp_raw)
            if timezone.is_naive(parsed_timestamp):
                parsed_timestamp = timezone.make_aware(
                    parsed_timestamp, timezone.get_current_timezone()
                )
        except ValueError:
            parsed_timestamp = None

    timestamp_label = _format_datetime(parsed_timestamp) if parsed_timestamp else timestamp_raw

    return {
        "timestamp": parsed_timestamp,
        "timestamp_raw": timestamp_raw,
        "timestamp_label": timestamp_label or timestamp_raw,
        "script": script,
        "event": event,
        "detail": detail,
        "raw": text,
    }


def _read_startup_report(
    *, limit: int | None = None, base_dir: Path | None = None
) -> dict[str, object]:
    normalized_limit = limit if limit is None or limit > 0 else None
    log_path = _startup_report_log_path(base_dir)
    lines: deque[str] = deque(maxlen=normalized_limit)

    try:
        with log_path.open(encoding="utf-8") as handle:
            for raw_line in handle:
                lines.append(raw_line.rstrip("\n"))
    except FileNotFoundError:
        return {
            "entries": [],
            "log_path": log_path,
            "missing": True,
            "error": _("Startup report log does not exist yet."),
            "limit": normalized_limit,
            "clock_warning": None,
        }
    except OSError as exc:
        return {
            "entries": [],
            "log_path": log_path,
            "missing": False,
            "error": str(exc),
            "limit": normalized_limit,
            "clock_warning": None,
        }

    parsed_entries = [
        entry for raw_line in lines if (entry := _parse_startup_report_entry(raw_line))
    ]
    parsed_entries.reverse()

    reference_time = _startup_report_reference_time(log_path) or timezone.now()
    clock_warning = None
    for entry in parsed_entries:
        timestamp = entry.get("timestamp")
        if not isinstance(timestamp, datetime):
            continue

        delta = timestamp - reference_time
        absolute_delta = delta if delta >= timedelta(0) else -delta
        if absolute_delta <= STARTUP_CLOCK_DRIFT_THRESHOLD:
            break

        offset_label = timesince(
            reference_time - absolute_delta, reference_time
        )
        direction = _("ahead") if delta > timedelta(0) else _("behind")
        clock_warning = _(
            "Startup timestamps appear %(offset)s %(direction)s of the current system time. "
            "Check the suite clock or NTP configuration."
        ) % {"offset": offset_label, "direction": direction}
        break

    return {
        "entries": parsed_entries,
        "log_path": log_path,
        "missing": False,
        "error": None,
        "limit": normalized_limit,
        "clock_warning": clock_warning,
    }

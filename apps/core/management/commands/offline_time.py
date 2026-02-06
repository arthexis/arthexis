from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.timesince import timesince

from apps.core.system.ui.formatters import format_datetime
from apps.core.system.ui.summary import (
    build_uptime_segments,
    load_shutdown_periods,
    suite_offline_period,
)


WINDOW_HOURS = 72


class Command(BaseCommand):
    help = "Summarize suite offline/online periods for the last 72 hours"

    def handle(self, *_args: Any, **_options: Any) -> None:
        now = timezone.now()
        window_start = now - timedelta(hours=WINDOW_HOURS)

        raw_periods, error = load_shutdown_periods()
        shutdown_periods: list[tuple[datetime, datetime]] = []
        for start, end in raw_periods:
            normalized_end = end or now
            if normalized_end < start:
                continue
            shutdown_periods.append((start, normalized_end))

        offline_period = suite_offline_period(now)
        if offline_period:
            shutdown_periods.append(offline_period)

        segments = build_uptime_segments(
            window_start=window_start,
            window_end=now,
            shutdown_periods=shutdown_periods,
        )

        window_duration = max((now - window_start).total_seconds(), 0.0)
        uptime_seconds = sum(
            segment["duration"].total_seconds()
            for segment in segments
            if segment["status"] == "up"
        )
        uptime_seconds = max(int(uptime_seconds), 0)
        downtime_seconds = max(int(window_duration - uptime_seconds), 0)

        self.stdout.write(
            f"Suite offline/online summary (last {WINDOW_HOURS} hours):"
        )
        self.stdout.write(
            f"  Window: {format_datetime(window_start)} to {format_datetime(now)}"
        )
        self.stdout.write("")
        self.stdout.write("Totals:")
        self.stdout.write(
            f"  Online: {_format_duration_hms(uptime_seconds)}"
        )
        self.stdout.write(
            f"  Offline: {_format_duration_hms(downtime_seconds)}"
        )
        self.stdout.write("")
        self.stdout.write("Timeline:")

        for segment in segments:
            status = segment["status"]
            start = segment["start"]
            end = segment["end"]
            status_label = "Online" if status == "up" else "Offline"
            duration_label = timesince(start, end)
            self.stdout.write(
                f"  - {status_label}: {format_datetime(start)} -> {format_datetime(end)} ({duration_label})"
            )

        if error:
            self.stderr.write(self.style.WARNING(f"Warning: {error}"))


def _format_duration_hms(seconds: int | None) -> str:
    if seconds is None or seconds < 0:
        return "?m?s"

    minutes_total, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes_total, 60)
    if hours:
        return f"{hours}h{minutes}m{secs}s"
    return f"{minutes}m{secs}s"

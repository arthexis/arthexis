from __future__ import annotations

from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.timesince import timesince

from apps.core.system.ui import (
    _build_uptime_segments,
    _format_datetime,
    _load_shutdown_periods,
    _suite_offline_period,
)
from apps.nodes import tasks as node_tasks


WINDOW_HOURS = 72


class Command(BaseCommand):
    help = "Summarize suite offline/online periods for the last 72 hours"

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        window_start = now - timedelta(hours=WINDOW_HOURS)

        raw_periods, error = _load_shutdown_periods()
        shutdown_periods: list[tuple[datetime, datetime]] = []
        for start, end in raw_periods:
            normalized_end = end or now
            if normalized_end < start:
                continue
            shutdown_periods.append((start, normalized_end))

        offline_period = _suite_offline_period(now)
        if offline_period:
            shutdown_periods.append(offline_period)

        segments = _build_uptime_segments(
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
            f"  Window: {_format_datetime(window_start)} to {_format_datetime(now)}"
        )
        self.stdout.write("")
        self.stdout.write("Totals:")
        self.stdout.write(
            f"  Online: {node_tasks._format_duration_hms(uptime_seconds)}"
        )
        self.stdout.write(
            f"  Offline: {node_tasks._format_duration_hms(downtime_seconds)}"
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
                f"  - {status_label}: {_format_datetime(start)} -> {_format_datetime(end)} ({duration_label})"
            )

        if error:
            self.stdout.write("")
            self.stdout.write(f"Warning: {error}")

from __future__ import annotations

import datetime as dt

from apps.energy.models.transactions import generate_missing_reports


class _DummySchedule:
    def __init__(self):
        self.runs: list[tuple[dt.date, dt.date]] = []

    def iter_pending_periods(self, reference=None):
        base = reference or dt.date(2024, 1, 15)
        return [
            (base - dt.timedelta(days=2), base - dt.timedelta(days=1)),
            (base, base + dt.timedelta(days=1)),
        ]

    def run(self, start, end):
        self.runs.append((start, end))
        return f"{start}:{end}"


def test_generate_missing_reports_runs_each_pending_period():
    schedule = _DummySchedule()
    results = generate_missing_reports(schedule)

    assert schedule.runs == schedule.iter_pending_periods()
    assert results == [f"{start}:{end}" for start, end in schedule.runs]

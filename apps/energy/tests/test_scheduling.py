from __future__ import annotations

import datetime as dt

import pytest

from apps.energy.models import ClientReportSchedule


def test_calculate_period_monthly_uses_previous_month():
    schedule = ClientReportSchedule(
        periodicity=ClientReportSchedule.PERIODICITY_MONTHLY,
        language="en",
    )
    start, end = schedule.calculate_period(reference=dt.date(2024, 5, 15))

    assert start == dt.date(2024, 4, 1)
    assert end == dt.date(2024, 4, 30)


@pytest.mark.django_db
def test_sync_periodic_task_creates_and_links_task():
    schedule = ClientReportSchedule(
        periodicity=ClientReportSchedule.PERIODICITY_DAILY,
        language="en",
    )
    schedule.save(sync_task=False)

    assert schedule.periodic_task_id is None

    schedule.sync_periodic_task()
    schedule.refresh_from_db()

    assert schedule.periodic_task is not None
    assert schedule.periodic_task.task == "apps.core.tasks.run_client_report_schedule"

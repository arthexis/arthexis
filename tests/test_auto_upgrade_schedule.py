from __future__ import annotations

import pytest
from django.utils import timezone

from apps.core import system
from apps.core.auto_upgrade import AUTO_UPGRADE_TASK_NAME, AUTO_UPGRADE_TASK_PATH


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("last_run_at", "expected"),
    [
        (None, ""),
        ("timestamp", "formatted"),
    ],
)
def test_auto_upgrade_schedule_reports_last_run(last_run_at, expected):
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).delete()

    schedule = IntervalSchedule.objects.create(every=5, period=IntervalSchedule.MINUTES)
    task = PeriodicTask.objects.create(
        name=AUTO_UPGRADE_TASK_NAME,
        task=AUTO_UPGRADE_TASK_PATH,
        interval=schedule,
        enabled=True,
    )

    if last_run_at == "timestamp":
        timestamp = timezone.now()
        task.last_run_at = timestamp
        task.save(update_fields=["last_run_at"])
        expected = system._format_timestamp(timestamp)

    info = system._load_auto_upgrade_schedule()

    assert info["configured"] is True
    assert info["available"] is True
    assert info["last_run_at"] == expected

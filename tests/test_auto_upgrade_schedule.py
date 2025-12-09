from __future__ import annotations

import pytest
from django.utils import timezone

from apps.core import system
from apps.core.auto_upgrade import AUTO_UPGRADE_TASK_NAME, AUTO_UPGRADE_TASK_PATH


@pytest.mark.django_db
def test_auto_upgrade_schedule_has_empty_last_run_before_first_execution():
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).delete()

    schedule = IntervalSchedule.objects.create(every=5, period=IntervalSchedule.MINUTES)
    PeriodicTask.objects.create(
        name=AUTO_UPGRADE_TASK_NAME,
        task=AUTO_UPGRADE_TASK_PATH,
        interval=schedule,
        enabled=True,
    )

    info = system._load_auto_upgrade_schedule()

    assert info["configured"] is True
    assert info["available"] is True
    assert info["last_run_at"] == ""


@pytest.mark.django_db
def test_auto_upgrade_schedule_reports_last_run_once_recorded():
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).delete()

    schedule = IntervalSchedule.objects.create(every=5, period=IntervalSchedule.MINUTES)
    task = PeriodicTask.objects.create(
        name=AUTO_UPGRADE_TASK_NAME,
        task=AUTO_UPGRADE_TASK_PATH,
        interval=schedule,
        enabled=True,
    )

    timestamp = timezone.now()
    task.last_run_at = timestamp
    task.save(update_fields=["last_run_at"])

    info = system._load_auto_upgrade_schedule()

    assert info["last_run_at"] == system._format_timestamp(timestamp)


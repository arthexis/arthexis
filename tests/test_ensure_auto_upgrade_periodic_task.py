from __future__ import annotations

from pathlib import Path

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.core.auto_upgrade import (
    AUTO_UPGRADE_CRONTAB_SCHEDULES,
    AUTO_UPGRADE_FAST_LANE_INTERVAL_MINUTES,
    AUTO_UPGRADE_TASK_NAME,
    AUTO_UPGRADE_TASK_PATH,
    ensure_auto_upgrade_periodic_task,
)


@pytest.mark.django_db

def test_removes_periodic_task_when_lock_missing(tmp_path: Path):
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    schedule = IntervalSchedule.objects.create(
        every=1,
        period=IntervalSchedule.MINUTES,
    )
    PeriodicTask.objects.create(
        name=AUTO_UPGRADE_TASK_NAME,
        task=AUTO_UPGRADE_TASK_PATH,
        interval=schedule,
    )

    with override_settings(BASE_DIR=tmp_path):
        ensure_auto_upgrade_periodic_task(base_dir=tmp_path)

    assert not PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).exists()


@pytest.mark.django_db

def test_creates_interval_schedule_with_override(monkeypatch, tmp_path: Path):
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "auto_upgrade.lck").write_text("stable", encoding="utf-8")
    monkeypatch.setenv("ARTHEXIS_UPGRADE_FREQ", "42")

    with override_settings(BASE_DIR=tmp_path):
        ensure_auto_upgrade_periodic_task(base_dir=tmp_path)

    task = PeriodicTask.objects.get(name=AUTO_UPGRADE_TASK_NAME)
    assert task.interval is not None
    assert task.interval.every == 42
    assert task.interval.period == IntervalSchedule.MINUTES
    assert task.crontab is None
    assert task.task == AUTO_UPGRADE_TASK_PATH


@pytest.mark.django_db

def test_attaches_crontab_for_valid_mode(tmp_path: Path):
    from django_celery_beat.models import CrontabSchedule, PeriodicTask

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "auto_upgrade.lck").write_text("stable", encoding="utf-8")

    with override_settings(BASE_DIR=tmp_path):
        ensure_auto_upgrade_periodic_task(base_dir=tmp_path)

    task = PeriodicTask.objects.get(name=AUTO_UPGRADE_TASK_NAME)
    assert task.interval is None
    assert task.crontab is not None

    expected_crontab = AUTO_UPGRADE_CRONTAB_SCHEDULES["stable"]
    schedule = CrontabSchedule.objects.get(id=task.crontab_id)
    assert schedule.minute == expected_crontab["minute"]
    assert schedule.hour == expected_crontab["hour"]
    assert schedule.day_of_week == expected_crontab["day_of_week"]
    assert schedule.day_of_month == expected_crontab["day_of_month"]
    assert schedule.month_of_year == expected_crontab["month_of_year"]
    assert str(schedule.timezone) == timezone.get_current_timezone_name()


@pytest.mark.django_db

def test_fast_lane_forces_hourly_interval(monkeypatch, tmp_path: Path):
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "auto_upgrade.lck").write_text("stable", encoding="utf-8")
    (lock_dir / "auto_upgrade_fast_lane.lck").touch()

    monkeypatch.setenv("ARTHEXIS_UPGRADE_FREQ", "1440")

    with override_settings(BASE_DIR=tmp_path):
        ensure_auto_upgrade_periodic_task(base_dir=tmp_path)

    task = PeriodicTask.objects.get(name=AUTO_UPGRADE_TASK_NAME)
    assert task.interval is not None
    assert task.interval.every == AUTO_UPGRADE_FAST_LANE_INTERVAL_MINUTES
    assert task.interval.period == IntervalSchedule.MINUTES
    assert task.crontab is None
    assert "Fast Lane" in (task.description or "")

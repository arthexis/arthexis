from pathlib import Path

from django.test import override_settings
import pytest

from apps.core.auto_upgrade import (
    AUTO_UPGRADE_INTERVAL_MINUTES,
    AUTO_UPGRADE_TASK_NAME,
    AUTO_UPGRADE_TASK_PATH,
    ensure_auto_upgrade_periodic_task,
)

pytestmark = pytest.mark.critical

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

    task = PeriodicTask.objects.get(name=AUTO_UPGRADE_TASK_NAME)
    assert task.interval is not None
    assert task.interval.every == AUTO_UPGRADE_INTERVAL_MINUTES["unstable"]

@pytest.mark.django_db

def test_creates_interval_schedule_with_override(monkeypatch, tmp_path: Path):
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

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
    from django_celery_beat.models import IntervalSchedule, PeriodicTask
    from apps.nodes.models import UpgradePolicy

    UpgradePolicy.objects.update_or_create(
        name="Stable",
        defaults={"channel": "stable", "interval_minutes": 30},
    )
    UpgradePolicy.objects.update_or_create(
        name="Unstable",
        defaults={"channel": "unstable", "interval_minutes": 60},
    )

    with override_settings(BASE_DIR=tmp_path):
        ensure_auto_upgrade_periodic_task(base_dir=tmp_path)

    task = PeriodicTask.objects.get(name=AUTO_UPGRADE_TASK_NAME)
    assert task.interval is not None
    assert task.interval.period == IntervalSchedule.MINUTES
    assert task.interval.every == 30

@pytest.mark.django_db

def test_fast_lane_forces_hourly_interval(monkeypatch, tmp_path: Path):
    from django_celery_beat.models import IntervalSchedule, PeriodicTask
    from apps.nodes.models import UpgradePolicy

    UpgradePolicy.objects.update_or_create(
        name="Fast Lane",
        defaults={"channel": "stable", "interval_minutes": 60},
    )

    monkeypatch.setenv("ARTHEXIS_UPGRADE_FREQ", "1440")

    with override_settings(BASE_DIR=tmp_path):
        ensure_auto_upgrade_periodic_task(base_dir=tmp_path)

    task = PeriodicTask.objects.get(name=AUTO_UPGRADE_TASK_NAME)
    assert task.interval is not None
    assert task.interval.every == 1440
    assert task.interval.period == IntervalSchedule.MINUTES
    assert task.crontab is None

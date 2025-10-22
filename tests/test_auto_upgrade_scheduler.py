import pytest

from django_celery_beat.models import IntervalSchedule, PeriodicTask

from core.auto_upgrade import ensure_auto_upgrade_periodic_task, AUTO_UPGRADE_TASK_NAME, AUTO_UPGRADE_TASK_PATH

pytestmark = [pytest.mark.feature("celery-queue")]


def test_ensure_auto_upgrade_task_skips_without_lock(tmp_path):
    PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).delete()

    ensure_auto_upgrade_periodic_task(base_dir=tmp_path)

    assert not PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).exists()


def test_ensure_auto_upgrade_task_uses_five_minute_interval_for_latest(tmp_path):
    PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).delete()

    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()
    (locks_dir / "auto_upgrade.lck").write_text("latest")

    ensure_auto_upgrade_periodic_task(base_dir=tmp_path)

    task = PeriodicTask.objects.get(name=AUTO_UPGRADE_TASK_NAME)
    assert task.task == AUTO_UPGRADE_TASK_PATH
    assert task.interval.every == 5
    assert task.interval.period == IntervalSchedule.MINUTES


def test_ensure_auto_upgrade_task_uses_five_minute_interval_for_version(tmp_path):
    PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).delete()

    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()
    (locks_dir / "auto_upgrade.lck").write_text("version")

    ensure_auto_upgrade_periodic_task(base_dir=tmp_path)

    task = PeriodicTask.objects.get(name=AUTO_UPGRADE_TASK_NAME)
    assert task.task == AUTO_UPGRADE_TASK_PATH
    assert task.interval.every == 5
    assert task.interval.period == IntervalSchedule.MINUTES

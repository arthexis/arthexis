import pytest

from django_celery_beat.models import IntervalSchedule, PeriodicTask

from core.auto_upgrade import (
    AUTO_UPGRADE_TASK_NAME,
    AUTO_UPGRADE_TASK_PATH,
    ensure_auto_upgrade_periodic_task,
)

pytestmark = [pytest.mark.feature("celery-queue")]


def test_ensure_auto_upgrade_task_skips_without_lock(tmp_path):
    PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).delete()

    ensure_auto_upgrade_periodic_task(base_dir=tmp_path)

    assert not PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).exists()


def test_ensure_auto_upgrade_task_is_removed_when_lock_deleted(tmp_path):
    PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).delete()

    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()
    lock_file = locks_dir / "auto_upgrade.lck"
    lock_file.write_text("version")

    ensure_auto_upgrade_periodic_task(base_dir=tmp_path)
    assert PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).exists()

    lock_file.unlink()
    ensure_auto_upgrade_periodic_task(base_dir=tmp_path)

    assert not PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).exists()

@pytest.mark.parametrize(
    ("mode", "expected_minutes"),
    [
        ("latest", 5),
        ("stable", 60),
        ("version", 720),
        ("", 720),
        ("UNSUPPORTED", 60),
    ],
    ids=[
        "latest-mode",
        "stable-mode",
        "version-mode",
        "empty-defaults-to-version",
        "unknown-fallback",
    ],
)
def test_ensure_auto_upgrade_task_uses_expected_interval(tmp_path, mode, expected_minutes):
    PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).delete()

    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()
    (locks_dir / "auto_upgrade.lck").write_text(mode)

    ensure_auto_upgrade_periodic_task(base_dir=tmp_path)

    task = PeriodicTask.objects.get(name=AUTO_UPGRADE_TASK_NAME)
    assert task.task == AUTO_UPGRADE_TASK_PATH
    assert task.interval.every == expected_minutes
    assert task.interval.period == IntervalSchedule.MINUTES


def test_ensure_auto_upgrade_task_updates_interval_when_mode_changes(tmp_path):
    PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).delete()

    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()
    mode_file = locks_dir / "auto_upgrade.lck"

    mode_file.write_text("latest")
    ensure_auto_upgrade_periodic_task(base_dir=tmp_path)
    task = PeriodicTask.objects.get(name=AUTO_UPGRADE_TASK_NAME)
    assert task.interval.every == 5

    mode_file.write_text("stable")
    ensure_auto_upgrade_periodic_task(base_dir=tmp_path)
    task.refresh_from_db()
    assert task.interval.every == 60

    mode_file.write_text("version")
    ensure_auto_upgrade_periodic_task(base_dir=tmp_path)
    task.refresh_from_db()
    assert task.interval.every == 720

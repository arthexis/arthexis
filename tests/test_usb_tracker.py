from __future__ import annotations

from pathlib import Path

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.recipes.models import Recipe
from apps.sensors import tasks as sensor_tasks
from apps.sensors.models import UsbTracker
from apps.sensors.scheduling import (
    USB_TRACKER_POLL_TASK_NAME,
    USB_TRACKER_POLL_TASK_PATH,
    ensure_usb_tracker_poll_task,
)
from apps.sensors.tasks import scan_usb_trackers


@pytest.mark.critical
@pytest.mark.django_db
def test_scan_usb_trackers_triggers_recipe(tmp_path, monkeypatch):
    mount_root = tmp_path / "media"
    mount_root.mkdir()
    device_mount = mount_root / "USB1"
    device_mount.mkdir()
    trigger_file = device_mount / "TRIGGER.txt"
    trigger_file.write_text("RUN", encoding="utf-8")

    recipe = Recipe.objects.create(
        slug="usb-recipe",
        display="USB Recipe",
        script="result = kwargs['file_path']",
        result_variable="result",
    )
    tracker = UsbTracker.objects.create(
        name="USB Trigger",
        slug="usb-trigger",
        required_file_path="TRIGGER.txt",
        recipe=recipe,
        cooldown_seconds=10,
        is_active=True,
    )

    fixed_now = timezone.now()
    monkeypatch.setattr(
        sensor_tasks.timezone,
        "localtime",
        lambda *args, **kwargs: fixed_now,
    )

    with override_settings(USB_TRACKER_MOUNT_ROOTS=[str(mount_root)]):
        result = scan_usb_trackers()

    tracker.refresh_from_db()
    assert result == {"scanned": 1, "matched": 1, "triggered": 1, "failed": 0}
    assert tracker.last_triggered_at == fixed_now
    assert tracker.last_match_path == str(trigger_file)
    assert tracker.last_recipe_result == str(trigger_file)
    assert tracker.last_error == ""


@pytest.mark.django_db
def test_ensure_usb_tracker_poll_task_uses_configured_interval(tmp_path: Path):
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "celery.lck").touch()

    with override_settings(BASE_DIR=tmp_path, USB_TRACKER_POLL_SECONDS=10):
        ensure_usb_tracker_poll_task()

    task = PeriodicTask.objects.get(name=USB_TRACKER_POLL_TASK_NAME)
    assert task.interval is not None
    assert task.interval.every == 10
    assert task.interval.period == IntervalSchedule.SECONDS
    assert task.task == USB_TRACKER_POLL_TASK_PATH

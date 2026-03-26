"""Regression tests for Playwright migration compatibility."""

from __future__ import annotations

import importlib

import pytest

pytestmark = [pytest.mark.django_db]


def test_followup_migration_removes_legacy_screenshot_periodic_tasks():
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    migration = importlib.import_module(
        "apps.playwright.migrations.0004_delete_websitescreenshotrun_and_more"
    )
    schedule = IntervalSchedule.objects.create(every=5, period=IntervalSchedule.SECONDS)
    PeriodicTask.objects.create(
        name="legacy-screenshot-sampling",
        task=migration.LEGACY_SCREENSHOT_TASK_PATH,
        interval=schedule,
    )
    PeriodicTask.objects.create(
        name="unrelated-task",
        task="apps.video.tasks.generate_thumbnail",
        interval=schedule,
    )

    migration.remove_legacy_screenshot_periodic_tasks(apps=None, schema_editor=None)

    assert not PeriodicTask.objects.filter(task=migration.LEGACY_SCREENSHOT_TASK_PATH).exists()
    assert PeriodicTask.objects.filter(task="apps.video.tasks.generate_thumbnail").count() == 1

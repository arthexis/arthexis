"""Regression tests for heartbeat task migration compatibility."""

from __future__ import annotations

import importlib

import pytest

pytestmark = [pytest.mark.django_db]


@pytest.fixture
def periodic_models():
    """Return django-celery-beat models used by the heartbeat migration tests."""

    from django_celery_beat.models import CrontabSchedule, PeriodicTask

    return CrontabSchedule, PeriodicTask


def test_followup_migration_rewrites_legacy_heartbeat_tasks(periodic_models):
    CrontabSchedule, PeriodicTask = periodic_models
    migration = importlib.import_module(
        "apps.celery.migrations.0004_migrate_legacy_heartbeat_task"
    )
    schedule = CrontabSchedule.objects.create(
        minute="*/5",
        hour="*",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
    )
    task = PeriodicTask.objects.create(
        name="legacy-heartbeat",
        task=migration.LEGACY_HEARTBEAT_TASK_PATH,
        crontab=schedule,
    )

    migration.migrate_heartbeat_periodic_tasks(apps=None, schema_editor=None)

    task.refresh_from_db()
    assert task.task == migration.CURRENT_HEARTBEAT_TASK_PATH


def test_followup_migration_restores_deleted_heartbeat_schedule(periodic_models):
    CrontabSchedule, PeriodicTask = periodic_models
    migration = importlib.import_module(
        "apps.celery.migrations.0004_migrate_legacy_heartbeat_task"
    )

    migration.migrate_heartbeat_periodic_tasks(apps=None, schema_editor=None)

    task = PeriodicTask.objects.get(name=migration.HEARTBEAT_TASK_NAME)
    assert task.task == migration.CURRENT_HEARTBEAT_TASK_PATH
    assert task.enabled is True
    assert task.crontab is not None
    assert task.crontab.minute == migration.HEARTBEAT_CRONTAB["minute"]
    assert task.crontab.hour == migration.HEARTBEAT_CRONTAB["hour"]
    assert PeriodicTask.objects.count() == 1

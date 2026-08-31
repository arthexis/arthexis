"""Tests for auto-upgrade periodic task loading and repair behavior."""

from __future__ import annotations

import builtins
import logging

import pytest

from apps.core.auto_upgrade import AUTO_UPGRADE_TASK_NAME
from apps.core.system.upgrade import _get_auto_upgrade_periodic_task
from django.db import DatabaseError


def test_get_auto_upgrade_periodic_task_handles_missing_django_celery_beat(
    monkeypatch,
    caplog,
):
    """Import failures should report dependency/configuration unavailability."""

    real_import = builtins.__import__

    def _import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "django_celery_beat.models":
            raise ImportError("missing django-celery-beat")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _import)

    with caplog.at_level(logging.ERROR):
        task, available, error = _get_auto_upgrade_periodic_task()

    assert task is None
    assert available is False
    assert error == "django-celery-beat is not installed or configured."
    assert "stage=import" in caplog.text
    assert "exception=ImportError" in caplog.text


@pytest.mark.django_db
def test_get_auto_upgrade_periodic_task_repairs_missing_task_row(monkeypatch):
    """Missing task rows should trigger a repair attempt and return the recreated task."""

    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).delete()

    def _ensure_task():
        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=15,
            period=IntervalSchedule.MINUTES,
        )
        PeriodicTask.objects.update_or_create(
            name=AUTO_UPGRADE_TASK_NAME,
            defaults={
                "interval": schedule,
                "task": "apps.core.tasks.auto_upgrade.check_github_updates",
            },
        )

    monkeypatch.setattr("apps.core.system.upgrade.ensure_auto_upgrade_periodic_task", _ensure_task)

    task, available, error = _get_auto_upgrade_periodic_task()

    assert task is not None
    assert task.name == AUTO_UPGRADE_TASK_NAME
    assert available is True
    assert error == ""


@pytest.mark.django_db
def test_get_auto_upgrade_periodic_task_reports_database_failure_during_repair(
    monkeypatch,
    caplog,
):
    """Database/config failures during repair should return the translated failure message."""

    from django_celery_beat.models import PeriodicTask

    PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).delete()

    def _fail_repair():
        raise DatabaseError("database unavailable")

    monkeypatch.setattr("apps.core.system.upgrade.ensure_auto_upgrade_periodic_task", _fail_repair)

    with caplog.at_level(logging.ERROR):
        task, available, error = _get_auto_upgrade_periodic_task()

    assert task is None
    assert available is False
    assert error == "Auto-upgrade schedule could not be loaded."
    assert "stage=repair" in caplog.text
    assert "exception=DatabaseError" in caplog.text

"""Regression tests for static Celery beat auto-upgrade scheduling."""

from __future__ import annotations

from celery import current_app
from celery.schedules import crontab
from django.conf import settings

from apps.core.auto_upgrade import AUTO_UPGRADE_CADENCE_HOUR, AUTO_UPGRADE_TASK_PATH


def test_auto_upgrade_policy_check_uses_static_daily_beat_schedule() -> None:
    """The live beat service uses the static scheduler, so this task must be here."""

    entry = settings.CELERY_BEAT_SCHEDULE["auto_upgrade_check"]

    assert entry["task"] == AUTO_UPGRADE_TASK_PATH
    assert entry["schedule"] == crontab(minute=0, hour=AUTO_UPGRADE_CADENCE_HOUR)


def test_auto_upgrade_policy_task_registers_under_static_schedule_name() -> None:
    from apps.nodes import tasks as _node_tasks

    del _node_tasks

    assert AUTO_UPGRADE_TASK_PATH in set(current_app.tasks.keys())

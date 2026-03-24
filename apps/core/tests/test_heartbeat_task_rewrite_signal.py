"""Regression tests for heartbeat rewrite signal behavior."""

from __future__ import annotations

import pytest

from apps.core import apps as core_apps

pytestmark = [pytest.mark.django_db]


def test_migrate_legacy_heartbeat_task_marks_beat_schedule_changed(monkeypatch):
    from django_celery_beat.models import CrontabSchedule, PeriodicTask, PeriodicTasks

    receivers: list[tuple[object, object]] = []

    def _capture_connect(receiver, sender=None, **kwargs):
        receivers.append((receiver, sender))
        return receiver

    monkeypatch.setattr("apps.celery.utils.is_celery_enabled", lambda: True)
    monkeypatch.setattr(
        "django.db.models.signals.post_migrate.connect",
        _capture_connect,
    )
    monkeypatch.setattr(
        "django.db.backends.signals.connection_created.connect",
        lambda *args, **kwargs: None,
    )

    core_apps._configure_lock_dependent_tasks(config=object())

    migrate_receiver = next(
        receiver
        for receiver, _ in receivers
        if getattr(receiver, "__name__", "") == "migrate_legacy_heartbeat_task"
    )

    schedule = CrontabSchedule.objects.create(
        minute="*/5",
        hour="*",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
    )
    task = PeriodicTask.objects.create(
        name="legacy-heartbeat-via-signal",
        task="core.tasks.heartbeat",
        crontab=schedule,
    )
    before = PeriodicTasks.last_change()

    migrate_receiver()

    task.refresh_from_db()
    after = PeriodicTasks.last_change()

    assert task.task == "apps.core.tasks.heartbeat"
    assert after is not None
    assert after != before

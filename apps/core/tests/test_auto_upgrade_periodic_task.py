"""Regression tests for auto-upgrade periodic task scheduling."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.auto_upgrade import (
    AUTO_UPGRADE_FEATURE_SLUG,
    AUTO_UPGRADE_TASK_NAME,
    AUTO_UPGRADE_TASK_PATH,
    ensure_auto_upgrade_periodic_task,
    sync_auto_upgrade_periodic_task_for_feature_change,
)

pytestmark = [pytest.mark.django_db]


def test_ensure_auto_upgrade_periodic_task_reuses_duplicate_interval_schedules(
    monkeypatch,
):
    """Auto-upgrade scheduling should tolerate duplicate interval schedule rows."""

    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    monkeypatch.delenv("ARTHEXIS_UPGRADE_FREQ", raising=False)
    interval_minutes = 113
    monkeypatch.setattr(
        "apps.core.auto_upgrade._resolve_policy_interval_minutes",
        lambda: interval_minutes,
    )
    IntervalSchedule.objects.filter(
        every=interval_minutes,
        period=IntervalSchedule.MINUTES,
    ).delete()

    schedule_one = IntervalSchedule.objects.create(
        every=interval_minutes,
        period=IntervalSchedule.MINUTES,
    )
    schedule_two = IntervalSchedule.objects.create(
        every=interval_minutes,
        period=IntervalSchedule.MINUTES,
    )
    if schedule_one.pk < schedule_two.pk:
        canonical_schedule = schedule_one
        duplicate_schedule = schedule_two
    else:
        canonical_schedule = schedule_two
        duplicate_schedule = schedule_one

    ensure_auto_upgrade_periodic_task()

    task = PeriodicTask.objects.get(name=AUTO_UPGRADE_TASK_NAME)
    assert task.interval_id == canonical_schedule.pk
    assert task.interval_id != duplicate_schedule.pk
    assert PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).count() == 1


def test_ensure_auto_upgrade_periodic_task_preserves_unchanged_task_metadata(
    monkeypatch,
):
    """No-op schedule syncs must not reset beat's interval run state."""

    from django_celery_beat.models import IntervalSchedule, PeriodicTask, PeriodicTasks

    monkeypatch.delenv("ARTHEXIS_UPGRADE_FREQ", raising=False)
    interval_minutes = 15
    monkeypatch.setattr(
        "apps.core.auto_upgrade._resolve_policy_interval_minutes",
        lambda: interval_minutes,
    )
    monkeypatch.setattr(
        "apps.core.auto_upgrade.auto_upgrade_suite_feature_enabled",
        lambda default=True: True,
    )
    schedule = IntervalSchedule.objects.create(
        every=interval_minutes,
        period=IntervalSchedule.MINUTES,
    )
    last_run_at = timezone.now() - timedelta(minutes=5)
    task = PeriodicTask.objects.create(
        name=AUTO_UPGRADE_TASK_NAME,
        interval=schedule,
        task="apps.nodes.tasks.apply_upgrade_policies",
        description=f"Upgrade policy checks run every {interval_minutes} minutes.",
        enabled=True,
        last_run_at=last_run_at,
        total_run_count=7,
    )
    date_changed = task.date_changed
    last_schedule_change = PeriodicTasks.last_change()

    ensure_auto_upgrade_periodic_task()

    task.refresh_from_db()
    assert task.last_run_at == last_run_at
    assert task.total_run_count == 7
    assert task.date_changed == date_changed
    assert PeriodicTasks.last_change() == last_schedule_change


def test_ensure_auto_upgrade_periodic_task_updates_only_scheduler_fields(
    monkeypatch,
):
    """Schedule repairs must not reset beat's execution counters."""

    from django_celery_beat.models import IntervalSchedule, PeriodicTask, PeriodicTasks

    monkeypatch.delenv("ARTHEXIS_UPGRADE_FREQ", raising=False)
    interval_minutes = 15
    monkeypatch.setattr(
        "apps.core.auto_upgrade._resolve_policy_interval_minutes",
        lambda: interval_minutes,
    )
    monkeypatch.setattr(
        "apps.core.auto_upgrade.auto_upgrade_suite_feature_enabled",
        lambda default=True: True,
    )
    stale_schedule = IntervalSchedule.objects.create(
        every=interval_minutes + 5,
        period=IntervalSchedule.MINUTES,
    )
    last_run_at = timezone.now() - timedelta(minutes=5)
    task = PeriodicTask.objects.create(
        name=AUTO_UPGRADE_TASK_NAME,
        interval=stale_schedule,
        task="apps.nodes.tasks.old_upgrade_policy",
        description="Old upgrade policy schedule.",
        enabled=True,
        last_run_at=last_run_at,
        total_run_count=7,
    )
    update_changed_calls = []
    original_update_changed = PeriodicTasks.update_changed

    def record_update_changed(**kwargs):
        update_changed_calls.append(kwargs)
        return original_update_changed(**kwargs)

    monkeypatch.setattr(
        PeriodicTasks,
        "update_changed",
        staticmethod(record_update_changed),
    )

    ensure_auto_upgrade_periodic_task()

    task.refresh_from_db()
    assert task.interval.every == interval_minutes
    assert task.interval.period == IntervalSchedule.MINUTES
    assert task.task == AUTO_UPGRADE_TASK_PATH
    assert task.description == (
        f"Upgrade policy checks run every {interval_minutes} minutes."
    )
    assert task.enabled is True
    assert task.last_run_at == last_run_at
    assert task.total_run_count == 7
    assert update_changed_calls == [{}]


def test_ensure_auto_upgrade_periodic_task_disables_task_when_feature_is_off(
    monkeypatch,
):
    """Regression: disabled auto-upgrade feature should disable beat scheduling."""

    from django_celery_beat.models import PeriodicTask

    monkeypatch.delenv("ARTHEXIS_UPGRADE_FREQ", raising=False)
    monkeypatch.setattr(
        "apps.core.auto_upgrade._resolve_policy_interval_minutes",
        lambda: 15,
    )
    monkeypatch.setattr(
        "apps.core.auto_upgrade.auto_upgrade_suite_feature_enabled",
        lambda default=True: False,
    )

    ensure_auto_upgrade_periodic_task()

    task = PeriodicTask.objects.get(name=AUTO_UPGRADE_TASK_NAME)
    assert task.enabled is False


def test_sync_auto_upgrade_periodic_task_for_feature_change_enables_task(
    monkeypatch,
):
    """Regression: enabling auto-upgrade feature should re-enable beat scheduling."""

    from django_celery_beat.models import PeriodicTask

    from apps.features.models import Feature

    monkeypatch.delenv("ARTHEXIS_UPGRADE_FREQ", raising=False)
    monkeypatch.setattr(
        "apps.core.auto_upgrade._resolve_policy_interval_minutes",
        lambda: 15,
    )

    feature = Feature.objects.create(
        slug=AUTO_UPGRADE_FEATURE_SLUG,
        display="Auto Upgrade",
        is_enabled=False,
    )

    ensure_auto_upgrade_periodic_task()
    task = PeriodicTask.objects.get(name=AUTO_UPGRADE_TASK_NAME)
    assert task.enabled is False

    feature.set_enabled(True)
    sync_auto_upgrade_periodic_task_for_feature_change(
        instance=feature,
        update_fields={"is_enabled"},
    )

    task.refresh_from_db()
    assert task.enabled is True

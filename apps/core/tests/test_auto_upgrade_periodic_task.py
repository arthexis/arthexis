"""Regression tests for auto-upgrade periodic task scheduling."""

from __future__ import annotations

import pytest

from apps.core.auto_upgrade import (
    AUTO_UPGRADE_FEATURE_SLUG,
    AUTO_UPGRADE_TASK_NAME,
    ensure_auto_upgrade_periodic_task,
    sync_auto_upgrade_periodic_task_for_feature_change,
)

pytestmark = [pytest.mark.django_db, pytest.mark.regression]


def test_ensure_auto_upgrade_periodic_task_reuses_duplicate_interval_schedules(
    monkeypatch,
):
    """Auto-upgrade scheduling should tolerate duplicate interval schedule rows."""

    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    monkeypatch.delenv("ARTHEXIS_UPGRADE_FREQ", raising=False)
    monkeypatch.setattr(
        "apps.core.auto_upgrade._resolve_policy_interval_minutes",
        lambda: 15,
    )

    first_schedule = IntervalSchedule.objects.create(
        every=15,
        period=IntervalSchedule.MINUTES,
    )
    duplicate_schedule = IntervalSchedule.objects.create(
        every=15,
        period=IntervalSchedule.MINUTES,
    )

    ensure_auto_upgrade_periodic_task()

    task = PeriodicTask.objects.get(name=AUTO_UPGRADE_TASK_NAME)
    assert task.interval_id == first_schedule.pk
    assert task.interval_id != duplicate_schedule.pk
    assert PeriodicTask.objects.filter(name=AUTO_UPGRADE_TASK_NAME).count() == 1


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

    from apps.features.models import Feature
    from django_celery_beat.models import PeriodicTask

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

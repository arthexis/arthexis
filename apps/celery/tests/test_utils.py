from __future__ import annotations

import pytest
from django.db.utils import IntegrityError

from apps.celery.utils import (
    get_or_create_crontab_schedule,
    resolve_crontab_timezone,
)

pytestmark = [pytest.mark.django_db]


def test_resolve_crontab_timezone_prefers_valid_celery_timezone(settings):
    settings.CELERY_TIMEZONE = "America/Monterrey"
    settings.TIME_ZONE = "UTC"

    assert resolve_crontab_timezone() == "America/Monterrey"


def test_resolve_crontab_timezone_falls_back_to_django_timezone(settings):
    settings.CELERY_TIMEZONE = "Invalid/Zone"
    settings.TIME_ZONE = "America/Monterrey"

    assert resolve_crontab_timezone() == "America/Monterrey"


def test_get_or_create_crontab_schedule_consolidates_timezone_duplicates(settings):
    from django_celery_beat.models import CrontabSchedule, PeriodicTask

    settings.CELERY_TIMEZONE = "America/Monterrey"
    cron_fields = {
        "minute": "17",
        "hour": "22",
        "day_of_week": "*",
        "day_of_month": "*",
        "month_of_year": "*",
    }
    PeriodicTask.objects.filter(crontab__isnull=False, crontab__minute="17").delete()
    CrontabSchedule.objects.filter(**cron_fields).delete()

    utc_schedule = CrontabSchedule.objects.create(**cron_fields, timezone="UTC")
    local_schedule = CrontabSchedule.objects.create(
        **cron_fields,
        timezone="America/Monterrey",
    )
    utc_task = PeriodicTask.objects.create(
        name="timezone-duplicate-utc",
        task="apps.core.tasks.utc",
        crontab=utc_schedule,
    )
    local_task = PeriodicTask.objects.create(
        name="timezone-duplicate-local",
        task="apps.core.tasks.local",
        crontab=local_schedule,
    )

    schedule, created = get_or_create_crontab_schedule(
        CrontabSchedule.objects,
        **cron_fields,
        managed_task_names={"timezone-duplicate-utc", "timezone-duplicate-local"},
    )

    assert created is False
    assert schedule.pk == local_schedule.pk
    assert str(schedule.timezone) == "America/Monterrey"
    assert CrontabSchedule.objects.filter(**cron_fields).count() == 1
    utc_task.refresh_from_db()
    local_task.refresh_from_db()
    assert utc_task.crontab_id == schedule.pk
    assert local_task.crontab_id == schedule.pk


def test_get_or_create_crontab_schedule_preserves_unmanaged_duplicate_tasks(settings):
    from django_celery_beat.models import CrontabSchedule, PeriodicTask

    settings.CELERY_TIMEZONE = "America/Monterrey"
    cron_fields = {
        "minute": "18",
        "hour": "22",
        "day_of_week": "*",
        "day_of_month": "*",
        "month_of_year": "*",
    }
    PeriodicTask.objects.filter(crontab__isnull=False, crontab__minute="18").delete()
    CrontabSchedule.objects.filter(**cron_fields).delete()

    utc_schedule = CrontabSchedule.objects.create(**cron_fields, timezone="UTC")
    local_schedule = CrontabSchedule.objects.create(
        **cron_fields,
        timezone="America/Monterrey",
    )
    managed_task = PeriodicTask.objects.create(
        name="managed-timezone-task",
        task="apps.core.tasks.managed",
        crontab=utc_schedule,
    )
    unmanaged_task = PeriodicTask.objects.create(
        name="operator-owned-timezone-task",
        task="apps.core.tasks.operator",
        crontab=utc_schedule,
    )

    schedule, created = get_or_create_crontab_schedule(
        CrontabSchedule.objects,
        **cron_fields,
        managed_task_names={"managed-timezone-task"},
    )

    assert created is False
    assert schedule.pk == local_schedule.pk
    assert CrontabSchedule.objects.filter(**cron_fields).count() == 2
    managed_task.refresh_from_db()
    unmanaged_task.refresh_from_db()
    assert managed_task.crontab_id == local_schedule.pk
    assert unmanaged_task.crontab_id == utc_schedule.pk


def test_get_or_create_crontab_schedule_promotes_legacy_timezone_row(settings):
    from django_celery_beat.models import CrontabSchedule

    settings.CELERY_TIMEZONE = "America/Monterrey"
    cron_fields = {
        "minute": "23",
        "hour": "22",
        "day_of_week": "*",
        "day_of_month": "*",
        "month_of_year": "*",
    }
    CrontabSchedule.objects.filter(**cron_fields).delete()
    legacy_schedule = CrontabSchedule.objects.create(**cron_fields, timezone="UTC")

    schedule, created = get_or_create_crontab_schedule(
        CrontabSchedule.objects,
        **cron_fields,
    )

    assert created is False
    assert schedule.pk == legacy_schedule.pk
    assert str(schedule.timezone) == "America/Monterrey"
    assert CrontabSchedule.objects.filter(**cron_fields).count() == 1


def test_get_or_create_crontab_schedule_notifies_when_promoting_timezone(
    monkeypatch,
    settings,
):
    from django_celery_beat.models import CrontabSchedule, PeriodicTasks

    settings.CELERY_TIMEZONE = "America/Monterrey"
    cron_fields = {
        "minute": "24",
        "hour": "22",
        "day_of_week": "*",
        "day_of_month": "*",
        "month_of_year": "*",
    }
    CrontabSchedule.objects.filter(**cron_fields).delete()
    legacy_schedule = CrontabSchedule.objects.create(**cron_fields, timezone="UTC")
    update_calls = []
    monkeypatch.setattr(PeriodicTasks, "update_changed", lambda: update_calls.append(True))

    schedule, created = get_or_create_crontab_schedule(
        CrontabSchedule.objects,
        **cron_fields,
    )

    assert created is False
    assert schedule.pk == legacy_schedule.pk
    assert update_calls == [True]


def test_get_or_create_crontab_schedule_refetches_after_concurrent_create(settings):
    settings.CELERY_TIMEZONE = "America/Monterrey"

    class FakeSchedule:
        pk = 99
        timezone = "America/Monterrey"

    class FakeQuery:
        def __iter__(self):
            return iter(())

        def order_by(self, *fields):
            return self

    class FakeManager:
        def __init__(self):
            self.schedule = FakeSchedule()
            self.create_calls = 0
            self.get_calls = 0
            self.select_for_update_calls = []

        def select_for_update(self, **kwargs):
            self.select_for_update_calls.append(kwargs)
            return self

        def filter(self, **kwargs):
            return FakeQuery()

        def create(self, **kwargs):
            self.create_calls += 1
            raise IntegrityError("duplicate schedule")

        def get(self, **kwargs):
            self.get_calls += 1
            return self.schedule

    manager = FakeManager()

    schedule, created = get_or_create_crontab_schedule(
        manager,
        minute="0",
        hour="2",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
    )

    assert created is False
    assert schedule is manager.schedule
    assert manager.create_calls == 1
    assert manager.get_calls == 1
    assert manager.select_for_update_calls == [{"of": ("self",)}, {"of": ("self",)}]

import json
from pathlib import Path

import pytest

from django.contrib import admin
from django.http import QueryDict
from django.test import RequestFactory
from django.utils import timezone
from django.conf import settings

from django_celery_beat.models import (
    CrontabSchedule,
    IntervalSchedule,
    PeriodicTask,
)


pytestmark = [pytest.mark.django_db, pytest.mark.feature("celery-queue")]


def _get_periodic_task_admin():
    return admin.site._registry[PeriodicTask]


def test_periodic_task_admin_columns_and_filters():
    PeriodicTask.objects.all().delete()

    admin_instance = _get_periodic_task_admin()
    assert admin_instance.list_display == (
        "name",
        "enabled",
        "scheduler",
        "interval",
        "last_run",
        "one_off",
    )
    assert "start_time" not in admin_instance.list_display

    assert admin_instance.list_filter[:4] == (
        "enabled",
        "one_off",
        "start_time",
        "last_run_at",
    )

    interval_filter_class = admin_instance.list_filter[-1]
    assert issubclass(interval_filter_class, admin.SimpleListFilter)
    assert interval_filter_class.parameter_name == "interval__period__exact"

    lookups = interval_filter_class(None, {}, admin_instance.model, admin_instance).lookups(
        None, admin_instance
    )
    assert list(lookups) == list(
        IntervalSchedule._meta.get_field("period").flatchoices
    )


def test_periodic_task_admin_last_run_format():
    PeriodicTask.objects.all().delete()

    admin_instance = _get_periodic_task_admin()
    interval = IntervalSchedule.objects.create(
        every=5, period=IntervalSchedule.MINUTES
    )
    last_run_at = timezone.now().replace(microsecond=123456)
    task = PeriodicTask.objects.create(
        name="horologia-last-run",
        task="core.tasks.heartbeat",
        interval=interval,
        last_run_at=last_run_at,
    )

    expected = timezone.localtime(task.last_run_at).replace(microsecond=0).isoformat()
    assert admin_instance.last_run(task) == expected

    task.last_run_at = None
    assert admin_instance.last_run(task) == ""


def test_interval_type_filter_querysets():
    PeriodicTask.objects.all().delete()

    admin_instance = _get_periodic_task_admin()
    rf = RequestFactory()
    request = rf.get("/admin/django_celery_beat/periodictask/")

    hourly = IntervalSchedule.objects.create(
        every=1, period=IntervalSchedule.HOURS
    )
    minutes = IntervalSchedule.objects.create(
        every=2, period=IntervalSchedule.MINUTES
    )

    hourly_task = PeriodicTask.objects.create(
        name="horologia-hourly",
        task="core.tasks.heartbeat",
        interval=hourly,
    )
    minutely_task = PeriodicTask.objects.create(
        name="horologia-minutely",
        task="core.tasks.heartbeat",
        interval=minutes,
    )

    interval_filter_class = admin_instance.list_filter[-1]
    params = QueryDict(mutable=True)
    params[interval_filter_class.parameter_name] = IntervalSchedule.HOURS
    interval_filter = interval_filter_class(
        request,
        params,
        admin_instance.model,
        admin_instance,
    )

    assert interval_filter.value() == IntervalSchedule.HOURS

    base_queryset = admin_instance.get_queryset(request).order_by("name")
    assert set(base_queryset) == {hourly_task, minutely_task}

    filtered = list(interval_filter.queryset(request, base_queryset))
    assert filtered == [hourly_task]


def test_ssl_renewal_fixture_loads_with_date_changed():
    PeriodicTask.objects.filter(name="renew-ssl-certificate").delete()
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="3",
        day_of_week="*",
        day_of_month="1",
        month_of_year="*",
        timezone="UTC",
    )

    fixture_path = (
        Path(settings.BASE_DIR)
        / "core"
        / "fixtures"
        / "celery_periodictask__renew_ssl_certificate.json"
    )

    payload = json.loads(fixture_path.read_text())
    assert payload, "Fixture must define at least one periodic task"
    record = payload[0]["fields"].copy()
    assert record["date_changed"], "Fixture should populate date_changed"

    record["crontab"] = schedule
    task = PeriodicTask.objects.create(**record)
    assert task.date_changed is not None


def test_periodic_task_fixture_signal_handles_legacy_names():
    PeriodicTask.objects.filter(name__icontains="renew").delete()
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="3",
        day_of_week="*",
        day_of_month="1",
        month_of_year="*",
        timezone="UTC",
    )
    legacy = PeriodicTask.objects.create(
        name="renew_ssl_certificate",
        task="core.tasks.renew_ssl_certificate",
        crontab=schedule,
    )

    duplicate = PeriodicTask(
        name="renew-ssl-certificate",
        task="core.tasks.renew_ssl_certificate",
        crontab=schedule,
        description="fixture update",
    )
    duplicate.save()

    legacy.refresh_from_db()
    assert legacy.pk == duplicate.pk
    assert legacy.name == "renew-ssl-certificate"
    assert legacy.description == "fixture update"


def test_periodic_task_fixture_loaddata_is_idempotent():
    PeriodicTask.objects.filter(name__icontains="renew").delete()
    fixture_path = (
        Path(settings.BASE_DIR)
        / "core"
        / "fixtures"
        / "celery_periodictask__renew_ssl_certificate.json"
    )

    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="3",
        day_of_week="*",
        day_of_month="1",
        month_of_year="*",
        timezone="UTC",
    )

    payload = json.loads(fixture_path.read_text())
    record = payload[0]["fields"].copy()
    record["crontab"] = schedule

    PeriodicTask.objects.create(**record)

    PeriodicTask.objects.filter(name="renew-ssl-certificate").update(enabled=False)

    reloaded = payload[0]["fields"].copy()
    reloaded["crontab"] = schedule
    PeriodicTask(**reloaded).save()

    refreshed = PeriodicTask.objects.get(name="renew-ssl-certificate")
    assert refreshed.enabled is True
    assert refreshed.crontab == schedule
    assert PeriodicTask.objects.filter(name="renew-ssl-certificate").count() == 1

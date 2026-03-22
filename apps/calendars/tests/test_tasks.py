from __future__ import annotations

from datetime import timedelta

import pytest
from celery.exceptions import NotRegistered
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from apps.calendars.models import CalendarEventDispatch, CalendarEventTrigger, GoogleCalendar
from apps.calendars.tasks import run_calendar_event_triggers
from apps.gdrive.models import GoogleAccount


@pytest.mark.django_db
def test_run_calendar_event_triggers_dispatches_once(monkeypatch):
    """Regression: trigger runner dispatches due events once per event revision."""
    user = get_user_model().objects.create_user(username="cal-user", password="x")
    account = GoogleAccount.objects.create(
        user=user,
        email="cal@example.com",
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
    )
    calendar = GoogleCalendar.objects.create(
        name="Ops",
        calendar_id="ops@example.com",
        account=account,
    )
    trigger = CalendarEventTrigger.objects.create(
        calendar=calendar,
        name="Run sampler",
        task_name="apps.content.tasks.run_scheduled_web_samplers",
        lead_time_minutes=0,
        summary_contains="Deploy",
    )

    now = timezone.now()
    event = {
        "id": "evt-1",
        "summary": "Deploy window",
        "updated": now.isoformat(),
        "start": {"dateTime": (now - timedelta(minutes=1)).isoformat()},
    }

    from apps.calendars import tasks as calendar_tasks
    from apps.calendars.services import GoogleCalendarGateway

    monkeypatch.setattr(
        GoogleCalendarGateway,
        "list_events",
        lambda self, window: [event],
    )

    calls = []

    def fake_send_task(name, kwargs=None, **extra):
        calls.append((name, kwargs))

    monkeypatch.setattr(calendar_tasks.current_app, "send_task", fake_send_task)

    count = run_calendar_event_triggers()
    count_second = run_calendar_event_triggers()

    assert count == 1
    assert count_second == 0
    assert len(calls) == 1
    assert calls[0][0] == trigger.task_name
    assert calls[0][1]["trigger_id"] == trigger.pk
    assert calls[0][1]["calendar_id"] == calendar.calendar_id
    assert CalendarEventDispatch.objects.count() == 1


@pytest.mark.django_db
def test_run_calendar_event_triggers_skips_unregistered_task(monkeypatch):
    """Unregistered tasks should not create dispatch records."""
    user = get_user_model().objects.create_user(username="cal-user-2", password="x")
    account = GoogleAccount.objects.create(
        user=user,
        email="cal2@example.com",
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
    )
    calendar = GoogleCalendar.objects.create(
        name="Ops2",
        calendar_id="ops2@example.com",
        account=account,
    )
    CalendarEventTrigger.objects.create(
        calendar=calendar,
        name="Broken task",
        task_name="apps.unknown.tasks.missing",
    )

    now = timezone.now()
    event = {
        "id": "evt-2",
        "summary": "Anything",
        "updated": now.isoformat(),
        "start": {"dateTime": (now - timedelta(minutes=1)).isoformat()},
    }

    from apps.calendars import tasks as calendar_tasks
    from apps.calendars.services import GoogleCalendarGateway

    monkeypatch.setattr(GoogleCalendarGateway, "list_events", lambda self, window: [event])

    def raise_not_registered(name, kwargs=None, **extra):
        raise NotRegistered(name)

    monkeypatch.setattr(calendar_tasks.current_app, "send_task", raise_not_registered)

    count = run_calendar_event_triggers()

    assert count == 0
    assert CalendarEventDispatch.objects.count() == 0


@pytest.mark.django_db
def test_run_calendar_event_triggers_handles_naive_datetime(monkeypatch):
    """Regression: naive event datetimes are normalized before due-time comparison."""
    user = get_user_model().objects.create_user(username="cal-user-3", password="x")
    account = GoogleAccount.objects.create(
        user=user,
        email="cal3@example.com",
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
    )
    calendar = GoogleCalendar.objects.create(
        name="Ops3",
        calendar_id="ops3@example.com",
        account=account,
    )
    trigger = CalendarEventTrigger.objects.create(
        calendar=calendar,
        name="Run naive",
        task_name="apps.content.tasks.run_scheduled_web_samplers",
        lead_time_minutes=0,
    )

    now = timezone.now()
    event = {
        "id": "evt-3",
        "summary": "Anything",
        "updated": now.replace(tzinfo=None).isoformat(),
        "start": {"dateTime": (now - timedelta(minutes=1)).replace(tzinfo=None).isoformat()},
    }

    from apps.calendars import tasks as calendar_tasks
    from apps.calendars.services import GoogleCalendarGateway

    monkeypatch.setattr(GoogleCalendarGateway, "list_events", lambda self, window: [event])

    calls = []

    def fake_send_task(name, kwargs=None, **extra):
        calls.append((name, kwargs))

    monkeypatch.setattr(calendar_tasks.current_app, "send_task", fake_send_task)

    count = run_calendar_event_triggers()

    assert count == 1
    assert len(calls) == 1
    dispatch = CalendarEventDispatch.objects.get(trigger=trigger, event_id="evt-3")
    assert timezone.is_aware(dispatch.event_updated)


@pytest.mark.django_db
@override_settings(CALENDAR_EVENT_TRIGGER_ALLOWED_TASKS=("apps.content.tasks.run_scheduled_web_samplers",))
def test_run_calendar_event_triggers_skips_disallowed_task(monkeypatch):
    """Security regression: disallowed trigger task names are never dispatched."""
    user = get_user_model().objects.create_user(username="cal-user-4", password="x")
    account = GoogleAccount.objects.create(
        user=user,
        email="cal4@example.com",
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
    )
    calendar = GoogleCalendar.objects.create(
        name="Ops4",
        calendar_id="ops4@example.com",
        account=account,
    )
    CalendarEventTrigger.objects.create(
        calendar=calendar,
        name="Disallowed",
        task_name="apps.ocpp.tasks.request_charge_point_log",
    )

    now = timezone.now()
    event = {
        "id": "evt-4",
        "summary": "Anything",
        "updated": now.isoformat(),
        "start": {"dateTime": (now - timedelta(minutes=1)).isoformat()},
    }

    from apps.calendars import tasks as calendar_tasks
    from apps.calendars.services import GoogleCalendarGateway

    monkeypatch.setattr(GoogleCalendarGateway, "list_events", lambda self, window: [event])

    calls = []

    def fake_send_task(name, kwargs=None, **extra):
        calls.append((name, kwargs))

    monkeypatch.setattr(calendar_tasks.current_app, "send_task", fake_send_task)

    count = run_calendar_event_triggers()

    assert count == 0
    assert calls == []
    assert CalendarEventDispatch.objects.count() == 0

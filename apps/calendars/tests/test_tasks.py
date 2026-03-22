from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.calendars.models import GoogleCalendar
from apps.calendars.tasks import push_calendar_event
from apps.gdrive.models import GoogleAccount


@pytest.mark.django_db
def test_push_calendar_event_creates_google_event(monkeypatch):
    """Calendar pushes should delegate outbound event creation to the gateway."""
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
        timezone="America/Chicago",
    )
    now = timezone.now().replace(microsecond=0)
    event_payload = {"id": "google-event-1", "htmlLink": "https://calendar.google.com/event?eid=1"}

    from apps.calendars.services import GoogleCalendarGateway

    captured = {}

    def fake_create_event(self, **kwargs):
        captured["calendar_id"] = self.calendar.calendar_id
        captured["kwargs"] = kwargs
        return event_payload

    monkeypatch.setattr(GoogleCalendarGateway, "create_event", fake_create_event)

    result = push_calendar_event(
        calendar.pk,
        summary="Deployment",
        starts_at=now.isoformat(),
        ends_at=(now + timedelta(minutes=30)).isoformat(),
        description="Deploy the new release",
        location="HQ",
        attendees=["a@example.com", "", "b@example.com"],
        metadata={"task_id": 7},
    )

    assert result == event_payload
    assert captured["calendar_id"] == calendar.calendar_id
    assert captured["kwargs"]["summary"] == "Deployment"
    assert captured["kwargs"]["timezone_name"] is None
    assert captured["kwargs"]["metadata"] == {"task_id": 7}


@pytest.mark.django_db
def test_push_calendar_event_rejects_disabled_calendar():
    """Disabled calendars should not receive new outbound event pushes."""
    user = get_user_model().objects.create_user(username="cal-user-2", password="x")
    account = GoogleAccount.objects.create(
        user=user,
        email="cal2@example.com",
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
    )
    calendar = GoogleCalendar.objects.create(
        name="Ops",
        calendar_id="ops2@example.com",
        account=account,
        is_enabled=False,
    )

    with pytest.raises(GoogleCalendar.DoesNotExist):
        push_calendar_event(
            calendar.pk,
            summary="Deployment",
            starts_at=timezone.now().isoformat(),
        )


def test_parse_datetime_input_accepts_datetime_instance():
    """The task helper should accept already-parsed datetimes for direct callers."""
    from apps.calendars.tasks import _parse_datetime_input

    value = timezone.now()

    assert _parse_datetime_input(value) is value

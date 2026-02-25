from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.calendars.models import CalendarEventSnapshot, GoogleCalendar
from apps.calendars.services import CalendarEventWindow, GoogleCalendarError, GoogleCalendarGateway
from apps.gdrive.models import GoogleAccount


@pytest.mark.django_db
def test_sync_event_snapshots_upserts_events(monkeypatch):
    """Snapshot sync should create or update snapshots from API events."""
    user = get_user_model().objects.create_user(username="cal-svc", password="x")
    account = GoogleAccount.objects.create(
        user=user,
        email="svc@example.com",
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
    )
    calendar = GoogleCalendar.objects.create(
        name="Ops",
        calendar_id="ops@example.com",
        account=account,
    )
    gateway = GoogleCalendarGateway(calendar)

    now = timezone.now()

    def fake_list_events(window):
        return [
            {
                "id": "evt-1",
                "summary": "Deploy",
                "location": "HQ",
                "updated": now.isoformat(),
                "start": {"dateTime": now.isoformat()},
                "end": {"dateTime": (now + timedelta(hours=1)).isoformat()},
            }
        ]

    monkeypatch.setattr(gateway, "list_events", fake_list_events)

    synced = gateway.sync_event_snapshots()

    assert synced == 1
    snapshot = CalendarEventSnapshot.objects.get(calendar=calendar, event_id="evt-1")
    assert snapshot.summary == "Deploy"
    assert snapshot.location == "HQ"


@pytest.mark.django_db
def test_gateway_requires_account():
    """Missing account should raise a GoogleCalendarError."""
    calendar = GoogleCalendar.objects.create(name="No Account", calendar_id="none@example.com")
    gateway = GoogleCalendarGateway(calendar)

    with pytest.raises(GoogleCalendarError):
        gateway.fetch_calendar_metadata()


@pytest.mark.django_db
def test_list_events_url_encodes_calendar_id(monkeypatch):
    """Regression: calendar IDs with reserved URL characters must be percent-encoded."""
    user = get_user_model().objects.create_user(username="cal-svc-2", password="x")
    account = GoogleAccount.objects.create(
        user=user,
        email="svc2@example.com",
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
    )
    calendar = GoogleCalendar.objects.create(
        name="Holidays",
        calendar_id="en.usa#holiday@group.calendar.google.com",
        account=account,
    )
    gateway = GoogleCalendarGateway(calendar)

    captured = {}

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        return {"items": []}

    monkeypatch.setattr(gateway, "_request", fake_request)

    now = timezone.now()
    gateway.list_events(window=CalendarEventWindow(time_min=now, time_max=now + timedelta(minutes=5)))

    assert captured["method"] == "GET"
    assert "#" not in captured["url"]
    assert "%23" in captured["url"]

from __future__ import annotations

from datetime import timedelta

import pytest
import requests
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.calendars.models import GoogleAccount, GoogleCalendar
from apps.calendars.services import GoogleCalendarError, GoogleCalendarGateway, GoogleCalendarRequestError
from apps.calendars.tasks import push_calendar_event


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


@pytest.mark.django_db
def test_google_calendar_requires_account_when_enabled():
    """Enabled outbound calendars must carry an account before they can validate."""
    calendar = GoogleCalendar(name="Ops", calendar_id="ops3@example.com", is_enabled=True)

    with pytest.raises(ValidationError):
        calendar.full_clean()


@pytest.mark.django_db
def test_google_calendar_gateway_normalizes_transport_errors(monkeypatch):
    """Transport failures should be surfaced through the domain-specific request error."""
    user = get_user_model().objects.create_user(username="cal-user-3", password="x")
    account = GoogleAccount.objects.create(
        user=user,
        email="cal3@example.com",
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
    )
    calendar = GoogleCalendar.objects.create(
        name="Ops",
        calendar_id="ops3@example.com",
        account=account,
    )
    gateway = GoogleCalendarGateway(calendar)

    monkeypatch.setattr(account, "get_access_token", lambda: "token")

    def fail_request(*args, **kwargs):
        raise requests.Timeout("boom")

    monkeypatch.setattr(requests, "request", fail_request)

    with pytest.raises(GoogleCalendarRequestError):
        gateway._request("POST", "https://example.com")


@pytest.mark.django_db
def test_google_calendar_gateway_rejects_inverted_event_windows():
    """The gateway should fail fast before posting an inverted event window."""
    user = get_user_model().objects.create_user(username="cal-user-4", password="x")
    account = GoogleAccount.objects.create(
        user=user,
        email="cal4@example.com",
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
    )
    calendar = GoogleCalendar.objects.create(
        name="Ops",
        calendar_id="ops4@example.com",
        account=account,
    )
    gateway = GoogleCalendarGateway(calendar)
    start = timezone.now().replace(microsecond=0)

    with pytest.raises(GoogleCalendarError, match="Event end must not be earlier than start"):
        gateway.create_event(
            summary="Deployment",
            starts_at=start,
            ends_at=start - timedelta(minutes=5),
        )


@pytest.mark.django_db
def test_google_account_reuses_cached_access_token_when_not_expired(monkeypatch):
    """Calendar OAuth accounts should reuse cached tokens until expiry."""
    user = get_user_model().objects.create_user(username="cal-user-5", password="x")
    account = GoogleAccount.objects.create(
        user=user,
        email="cal5@example.com",
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
        access_token="cached-token",
        token_expires_at=timezone.now() + timedelta(minutes=5),
    )

    def fail_post(*args, **kwargs):
        raise AssertionError("OAuth refresh should not be called while the token is still valid")

    monkeypatch.setattr(requests, "post", fail_post)

    assert account.get_access_token() == "cached-token"

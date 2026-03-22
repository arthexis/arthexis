from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from typing import Any, Iterable
from urllib.parse import quote

import requests
from django.utils import timezone

from .models import GoogleCalendar


class GoogleCalendarError(RuntimeError):
    """Base exception raised for Google Calendar integration failures."""


class GoogleCalendarRequestError(GoogleCalendarError):
    """Raised when Google Calendar API returns an error response."""


class GoogleCalendarGateway:
    """Gateway wrapping outbound Google Calendar event publishing."""

    base_url = "https://www.googleapis.com/calendar/v3/calendars"

    def __init__(self, calendar: GoogleCalendar):
        self.calendar = calendar
        self.account = calendar.account

    def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        if not self.account:
            raise GoogleCalendarError("Calendar destination has no Google account configured.")
        token = self.account.get_access_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        headers.setdefault("Accept", "application/json")
        headers.setdefault("Content-Type", "application/json")
        try:
            response = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        except requests.RequestException as exc:
            raise GoogleCalendarRequestError(
                "Google Calendar API request failed before a response was received."
            ) from exc
        if response.status_code >= 400:
            raise GoogleCalendarRequestError(
                f"Google Calendar API request failed ({response.status_code}): {response.text}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise GoogleCalendarRequestError("Google Calendar API returned a non-object payload.")
        return payload

    def create_event(
        self,
        *,
        summary: str,
        starts_at: datetime,
        ends_at: datetime | None = None,
        description: str = "",
        location: str = "",
        attendees: Iterable[str] = (),
        timezone_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create an event on the configured Google Calendar and return the API payload."""
        start = _coerce_aware_datetime(starts_at)
        end = _coerce_aware_datetime(ends_at) if ends_at else None
        if end is not None and end < start:
            raise GoogleCalendarError("Event end must not be earlier than start.")
        calendar_id = quote(self.calendar.calendar_id, safe="")
        timezone_value = timezone_name or self.calendar.timezone or timezone.get_current_timezone_name()
        body: dict[str, Any] = {
            "summary": summary,
            "start": {
                "dateTime": start.isoformat(),
                "timeZone": timezone_value,
            },
        }
        if end is not None:
            body["end"] = {
                "dateTime": end.isoformat(),
                "timeZone": timezone_value,
            }
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        attendee_list = [str(email).strip() for email in attendees if str(email).strip()]
        if attendee_list:
            body["attendees"] = [{"email": email} for email in attendee_list]
        if metadata:
            body["extendedProperties"] = {"private": {k: str(v) for k, v in metadata.items()}}
        return self._request("POST", f"{self.base_url}/{calendar_id}/events", json=body)


def _coerce_aware_datetime(value: datetime) -> datetime:
    """Return an aware datetime, assuming UTC for naive values."""
    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone=dt_timezone.utc)
    return value

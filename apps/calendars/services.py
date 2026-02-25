from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any
from urllib.parse import quote

import requests
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import CalendarEventSnapshot, GoogleCalendar


class GoogleCalendarError(RuntimeError):
    """Base exception raised for Google Calendar integration failures."""


class GoogleCalendarRequestError(GoogleCalendarError):
    """Raised when Google Calendar API returns an error response."""


@dataclass(frozen=True)
class CalendarEventWindow:
    """Time window used to query calendar events from Google API."""

    time_min: datetime
    time_max: datetime


class GoogleCalendarGateway:
    """Gateway wrapping Google Calendar API operations for tracked calendars."""

    base_url = "https://www.googleapis.com/calendar/v3/calendars"

    def __init__(self, calendar: GoogleCalendar):
        self.calendar = calendar
        self.account = calendar.account

    def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        if not self.account:
            raise GoogleCalendarError("Tracked calendar has no Google account configured.")
        token = self.account.get_access_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        headers.setdefault("Accept", "application/json")
        response = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        if response.status_code >= 400:
            raise GoogleCalendarRequestError(
                f"Google Calendar API request failed ({response.status_code}): {response.text}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise GoogleCalendarRequestError("Google Calendar API returned a non-object payload.")
        return payload

    def fetch_calendar_metadata(self) -> dict[str, Any]:
        """Fetch and persist metadata for the tracked calendar."""
        calendar_id = quote(self.calendar.calendar_id, safe="")
        payload = self._request("GET", f"{self.base_url}/{calendar_id}")
        self.calendar.name = payload.get("summary") or self.calendar.name
        self.calendar.timezone = payload.get("timeZone") or self.calendar.timezone
        self.calendar.metadata = payload
        self.calendar.save(update_fields=["name", "timezone", "metadata"])
        return payload

    def list_events(self, window: CalendarEventWindow) -> list[dict[str, Any]]:
        """List events between ``time_min`` and ``time_max`` ordered by start time."""
        calendar_id = quote(self.calendar.calendar_id, safe="")
        payload = self._request(
            "GET",
            f"{self.base_url}/{calendar_id}/events",
            params={
                "singleEvents": "true",
                "orderBy": "startTime",
                "timeMin": window.time_min.astimezone(dt_timezone.utc).isoformat(),
                "timeMax": window.time_max.astimezone(dt_timezone.utc).isoformat(),
            },
        )
        items = payload.get("items") or []
        return [item for item in items if isinstance(item, dict)]

    @transaction.atomic
    def sync_event_snapshots(self, horizon_minutes: int = 1440) -> int:
        """Synchronize upcoming events into local snapshot records."""
        now = timezone.now()
        window = CalendarEventWindow(time_min=now - timedelta(minutes=5), time_max=now + timedelta(minutes=horizon_minutes))
        events = self.list_events(window)
        synced = 0
        for event in events:
            event_id = str(event.get("id") or "").strip()
            if not event_id:
                continue
            starts_at = _extract_event_datetime(event.get("start") or {})
            ends_at = _extract_event_datetime(event.get("end") or {})
            updated = parse_datetime(str(event.get("updated") or ""))
            if updated is None:
                updated = now
            CalendarEventSnapshot.objects.update_or_create(
                calendar=self.calendar,
                event_id=event_id,
                defaults={
                    "summary": str(event.get("summary") or ""),
                    "location": str(event.get("location") or ""),
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                    "event_updated": updated,
                    "raw": event,
                },
            )
            synced += 1
        return synced


def _extract_event_datetime(data: dict[str, Any]) -> datetime | None:
    """Extract RFC3339 datetime from Google event ``start``/``end`` payload."""
    date_time = data.get("dateTime")
    if date_time:
        parsed = parse_datetime(str(date_time))
        if parsed is not None:
            return parsed
    date_only = data.get("date")
    if date_only:
        parsed = parse_datetime(f"{date_only}T00:00:00+00:00")
        if parsed is not None:
            return parsed
    return None

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from celery import shared_task

from .models import GoogleCalendar
from .services import GoogleCalendarGateway


@shared_task(name="apps.calendars.tasks.push_calendar_event")
def push_calendar_event(
    calendar_pk: int,
    *,
    summary: str,
    starts_at: str,
    ends_at: str | None = None,
    description: str = "",
    location: str = "",
    attendees: Iterable[str] = (),
    timezone_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Push an event to an external Google Calendar without persisting a local record."""
    calendar = GoogleCalendar.objects.get(pk=calendar_pk, is_enabled=True)
    gateway = GoogleCalendarGateway(calendar)
    return gateway.create_event(
        summary=summary,
        starts_at=_parse_datetime_input(starts_at),
        ends_at=_parse_datetime_input(ends_at) if ends_at else None,
        description=description,
        location=location,
        attendees=attendees,
        timezone_name=timezone_name,
        metadata=metadata,
    )


def _parse_datetime_input(value: str | datetime) -> datetime:
    """Accept ISO 8601 strings or datetime objects for outbound event pushes."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)

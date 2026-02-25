from __future__ import annotations

from datetime import timedelta

from celery import current_app, shared_task
from celery.exceptions import NotRegistered
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import CalendarEventDispatch, CalendarEventTrigger, GoogleCalendar
from .services import CalendarEventWindow, GoogleCalendarGateway, _extract_event_datetime


@shared_task(name="apps.calendars.tasks.sync_google_calendars")
def sync_google_calendars(horizon_minutes: int = 1440) -> int:
    """Synchronize snapshots for each enabled tracked calendar."""
    synced = 0
    calendars = GoogleCalendar.objects.filter(is_enabled=True).select_related("account")
    for calendar in calendars:
        gateway = GoogleCalendarGateway(calendar)
        try:
            gateway.fetch_calendar_metadata()
            synced += gateway.sync_event_snapshots(horizon_minutes=horizon_minutes)
        except (ValidationError, RuntimeError):
            continue
    return synced


@shared_task(name="apps.calendars.tasks.run_calendar_event_triggers")
def run_calendar_event_triggers() -> int:
    """Dispatch configured Celery tasks when matching events are due."""
    dispatched = 0
    triggers = (
        CalendarEventTrigger.objects.filter(is_enabled=True, calendar__is_enabled=True)
        .select_related("calendar", "calendar__account")
    )
    now = timezone.now()
    for trigger in triggers:
        horizon = now + timedelta(minutes=max(trigger.lead_time_minutes, 0) + 2)
        window = CalendarEventWindow(time_min=now - timedelta(minutes=2), time_max=horizon)
        gateway = GoogleCalendarGateway(trigger.calendar)
        try:
            events = gateway.list_events(window)
        except (ValidationError, RuntimeError):
            continue

        for event in events:
            event_id = str(event.get("id") or "").strip()
            if not event_id:
                continue
            if not _event_matches_trigger(trigger, event):
                continue
            start_at = _extract_event_datetime(event.get("start") or {})
            if start_at is None:
                continue
            execute_at = start_at - timedelta(minutes=trigger.lead_time_minutes)
            if execute_at > now:
                continue

            event_updated = _extract_event_updated(event, default=now)
            if CalendarEventDispatch.objects.filter(
                trigger=trigger,
                event_id=event_id,
                event_updated=event_updated,
            ).exists():
                continue

            kwargs = {
                "calendar_event": event,
                "calendar_id": trigger.calendar_id,
                "trigger_id": trigger.pk,
            }
            try:
                current_app.send_task(trigger.task_name, kwargs=kwargs)
            except NotRegistered:
                continue
            CalendarEventDispatch.objects.create(
                trigger=trigger,
                event_id=event_id,
                event_updated=event_updated,
            )
            dispatched += 1
    return dispatched


def _extract_event_updated(event: dict, default):
    """Extract event update timestamp with fallback to ``default``."""
    from django.utils.dateparse import parse_datetime

    updated = parse_datetime(str(event.get("updated") or ""))
    return updated or default


def _event_matches_trigger(trigger: CalendarEventTrigger, event: dict) -> bool:
    """Return ``True`` if event summary/location satisfy trigger filters."""
    summary = str(event.get("summary") or "").casefold()
    location = str(event.get("location") or "").casefold()
    if trigger.summary_contains and trigger.summary_contains.casefold() not in summary:
        return False
    if trigger.location_contains and trigger.location_contains.casefold() not in location:
        return False
    return True

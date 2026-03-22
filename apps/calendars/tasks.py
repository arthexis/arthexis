from __future__ import annotations

from datetime import timedelta, timezone as dt_timezone

from celery import current_app, shared_task
from celery.exceptions import NotRegistered
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import CalendarEventDispatch, CalendarEventTrigger, GoogleCalendar


from .services import (
    CalendarEventWindow,
    GoogleCalendarError,
    GoogleCalendarGateway,
    _extract_event_datetime,
)


def _allowed_calendar_trigger_tasks() -> set[str]:
    """Return allowlisted Celery task names for calendar event triggers."""
    return {
        str(task).strip()
        for task in getattr(settings, "CALENDAR_EVENT_TRIGGER_ALLOWED_TASKS", ())
        if str(task).strip()
    }


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
        except GoogleCalendarError:
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
    allowed_tasks = _allowed_calendar_trigger_tasks()
    for trigger in triggers:
        if trigger.task_name not in allowed_tasks:
            continue
        horizon = now + timedelta(minutes=max(trigger.lead_time_minutes, 0) + 2)
        window = CalendarEventWindow(time_min=now - timedelta(minutes=2), time_max=horizon)
        gateway = GoogleCalendarGateway(trigger.calendar)
        try:
            events = gateway.list_events(window)
        except GoogleCalendarError:
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
            if timezone.is_naive(start_at):
                start_at = timezone.make_aware(start_at, timezone=dt_timezone.utc)
            execute_at = start_at - timedelta(minutes=trigger.lead_time_minutes)
            if execute_at > now:
                continue

            event_updated = _extract_event_updated(event, default=now)
            try:
                with transaction.atomic():
                    CalendarEventDispatch.objects.create(
                        trigger=trigger,
                        event_id=event_id,
                        event_updated=event_updated,
                    )
            except IntegrityError:
                continue

            kwargs = {
                "calendar_event": event,
                "calendar_id": trigger.calendar.calendar_id,
                "trigger_id": trigger.pk,
            }
            try:
                current_app.send_task(trigger.task_name, kwargs=kwargs)
            except NotRegistered:
                CalendarEventDispatch.objects.filter(
                    trigger=trigger,
                    event_id=event_id,
                    event_updated=event_updated,
                ).delete()
                continue
            dispatched += 1
    return dispatched


def _extract_event_updated(event: dict, default):
    """Extract event update timestamp with fallback to ``default``."""
    updated = parse_datetime(str(event.get("updated") or ""))
    if updated is not None and timezone.is_naive(updated):
        updated = timezone.make_aware(updated, timezone=dt_timezone.utc)
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

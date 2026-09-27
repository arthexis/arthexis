"""Observe charger event-time progress without controlling intake."""

from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.events.services import publish_safely
from apps.ocpp.models import Charger, ChargerTimelineProgress
from apps.ocpp.services.timeline_status import timeline_snapshot

LIVE_WINDOW = timedelta(minutes=15)


def observe_timeline(
    *,
    charger: Charger,
    event_at: datetime | None,
    received_at: datetime | None = None,
) -> ChargerTimelineProgress:
    received_at = received_at or timezone.now()
    progress, _ = ChargerTimelineProgress.objects.get_or_create(charger=charger)
    historical = event_at is not None and event_at < received_at - LIVE_WINDOW
    updates = {"last_received_at": received_at, "observed_events": F("observed_events") + 1}
    if historical:
        updates["historical_events_seen"] = F("historical_events_seen") + 1
    if event_at is not None and (progress.newest_event_at is None or event_at > progress.newest_event_at):
        updates["newest_event_at"] = event_at
    ChargerTimelineProgress.objects.filter(pk=progress.pk).update(**updates)
    progress.refresh_from_db()
    if progress.newest_event_at is None:
        state = ChargerTimelineProgress.State.UNKNOWN
    elif progress.newest_event_at >= received_at - LIVE_WINDOW:
        state = ChargerTimelineProgress.State.LIVE
    elif progress.historical_events_seen <= 1:
        state = ChargerTimelineProgress.State.HISTORICAL
    else:
        state = ChargerTimelineProgress.State.CATCHING_UP
    if progress.state != state:
        previous_state = progress.state
        progress.state = state
        progress.save(update_fields=("state", "updated_at"))
        payload = timeline_snapshot(progress)
        payload["previous_state"] = previous_state
        transaction.on_commit(
            lambda payload=payload: publish_safely(
                event_type="ocpp.timeline.state_changed",
                producer="ocpp.timeline",
                payload=payload,
            )
        )
    return progress

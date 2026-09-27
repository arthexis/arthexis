"""Read-only operator snapshots for charger timeline health."""

from datetime import datetime

from django.utils import timezone

from apps.ocpp.models import Charger, ChargerTimelineProgress


def timeline_status(
    charger: Charger, *, now: datetime | None = None
) -> dict[str, object]:
    """Return current timeline health without creating or changing state."""
    current = now or timezone.now()
    try:
        progress = charger.timeline_progress
    except ChargerTimelineProgress.DoesNotExist:
        return _empty_snapshot(charger=charger, as_of=current)
    return timeline_snapshot(progress, as_of=current)


def timeline_snapshot(
    progress: ChargerTimelineProgress, *, as_of: datetime | None = None
) -> dict[str, object]:
    """Return a JSON-safe status payload for displays and other observers."""
    current = as_of or progress.last_received_at or timezone.now()
    return {
        "charger_id": progress.charger_id,
        "charger_identity": progress.charger.identity,
        "state": progress.state,
        "as_of": _iso(current),
        "newest_event_at": _iso(progress.newest_event_at),
        "last_received_at": _iso(progress.last_received_at),
        "event_lag_seconds": _age(current, progress.newest_event_at),
        "receipt_age_seconds": _age(current, progress.last_received_at),
        "historical_events_seen": progress.historical_events_seen,
        "observed_events": progress.observed_events,
    }


def _empty_snapshot(*, charger: Charger, as_of: datetime) -> dict[str, object]:
    return {
        "charger_id": charger.pk,
        "charger_identity": charger.identity,
        "state": ChargerTimelineProgress.State.UNKNOWN,
        "as_of": _iso(as_of),
        "newest_event_at": None,
        "last_received_at": None,
        "event_lag_seconds": None,
        "receipt_age_seconds": None,
        "historical_events_seen": 0,
        "observed_events": 0,
    }


def _age(current: datetime, value: datetime | None) -> float | None:
    if value is None:
        return None
    return max(0.0, (current - value).total_seconds())


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None

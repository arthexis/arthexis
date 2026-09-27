"""Read-only operator snapshots for charger timeline health."""

from datetime import datetime

from apps.ocpp.models import ChargerTimelineProgress


def timeline_snapshot(progress: ChargerTimelineProgress) -> dict[str, object]:
    """Return a JSON-safe status payload for displays and other observers."""
    return {
        "charger_id": progress.charger_id,
        "state": progress.state,
        "newest_event_at": _iso(progress.newest_event_at),
        "last_received_at": _iso(progress.last_received_at),
        "historical_events_seen": progress.historical_events_seen,
        "observed_events": progress.observed_events,
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None

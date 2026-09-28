"""Read-only operator snapshots for charger timeline health."""

from datetime import datetime, timedelta

from django.utils import timezone

from apps.ocpp.models import (
    Charger,
    ChargerTimelineProgress,
    InboundProtocolRequest,
    ProtocolOperation,
)

STALE_AFTER = timedelta(minutes=15)
METRIC_WINDOW = timedelta(minutes=5)


def query_timeline_status(
    identity: str, *, now: datetime | None = None
) -> dict[str, object]:
    """Resolve a charger by its public identity and return read-only health."""
    charger = Charger.objects.get_by_natural_key(identity)
    return timeline_status(charger, now=now)


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
    receipt_age = _age(current, progress.last_received_at)
    snapshot = {
        "charger_id": progress.charger_id,
        "charger_identity": progress.charger.identity,
        "state": progress.state,
        "condition": operator_condition(progress, as_of=current),
        "as_of": _iso(current),
        "newest_event_at": _iso(progress.newest_event_at),
        "last_received_at": _iso(progress.last_received_at),
        "event_lag_seconds": _age(current, progress.newest_event_at),
        "receipt_age_seconds": receipt_age,
        "historical_events_seen": progress.historical_events_seen,
        "observed_events": progress.observed_events,
    }
    snapshot.update(_operational_metrics(progress.charger, as_of=current))
    return snapshot


def operator_condition(
    progress: ChargerTimelineProgress, *, as_of: datetime | None = None
) -> str:
    """Translate timeline evidence into a stable operator-facing condition."""
    current = as_of or progress.last_received_at or timezone.now()
    receipt_age = _age(current, progress.last_received_at)
    if receipt_age is None or progress.state == ChargerTimelineProgress.State.UNKNOWN:
        return "unknown"

    stale = receipt_age > STALE_AFTER.total_seconds()
    if progress.state == ChargerTimelineProgress.State.LIVE:
        return "degraded" if stale else "ready"
    if progress.state in {
        ChargerTimelineProgress.State.HISTORICAL,
        ChargerTimelineProgress.State.CATCHING_UP,
    }:
        return "stalled" if stale else "catching_up"
    return "unknown"


def _empty_snapshot(*, charger: Charger, as_of: datetime) -> dict[str, object]:
    return {
        "charger_id": charger.pk,
        "charger_identity": charger.identity,
        "state": ChargerTimelineProgress.State.UNKNOWN,
        "condition": "unknown",
        "as_of": _iso(as_of),
        "newest_event_at": None,
        "last_received_at": None,
        "event_lag_seconds": None,
        "receipt_age_seconds": None,
        "historical_events_seen": 0,
        "observed_events": 0,
        **_operational_metrics(charger, as_of=as_of),
    }


def _operational_metrics(charger: Charger, *, as_of: datetime) -> dict[str, object]:
    window_start = as_of - METRIC_WINDOW
    recent_inbound = charger.inbound_protocol_requests.filter(
        received_at__gte=window_start,
        received_at__lte=as_of,
    )
    completed = recent_inbound.filter(status=InboundProtocolRequest.Status.COMPLETED).count()
    request_errors = recent_inbound.filter(
        response_kind=InboundProtocolRequest.ResponseKind.ERROR
    ).count()
    inbound_pending = charger.inbound_protocol_requests.filter(
        status=InboundProtocolRequest.Status.PROCESSING,
        received_at__lte=as_of,
    )
    inbound_processing = inbound_pending.count()

    pressure_statuses = (
        ProtocolOperation.Status.PENDING,
        ProtocolOperation.Status.DELIVERING,
        ProtocolOperation.Status.RECOVERY_REQUIRED,
    )
    outbound_pending = charger.protocol_operations.filter(
        status__in=pressure_statuses,
        created_at__lte=as_of,
    )
    outbound_pressure = outbound_pending.count()
    recent_outbound = charger.protocol_operations.filter(
        created_at__gte=window_start,
        created_at__lte=as_of,
    )
    outbound_errors = recent_outbound.filter(
        status__in=(
            ProtocolOperation.Status.ERRORED,
            ProtocolOperation.Status.TIMED_OUT,
            ProtocolOperation.Status.DISCONNECTED,
        )
    ).count()
    retry_attempts = sum(
        max(0, attempt_count - 1)
        for attempt_count in recent_outbound.values_list("attempt_count", flat=True)
    )

    oldest_inbound = inbound_pending.order_by("received_at").values_list(
        "received_at", flat=True
    ).first()
    oldest_outbound = outbound_pending.order_by("created_at").values_list(
        "created_at", flat=True
    ).first()
    oldest_pending = min(
        (value for value in (oldest_inbound, oldest_outbound) if value is not None),
        default=None,
    )

    connection = getattr(charger, "connection", None)
    lease_remaining = (
        max(0.0, (connection.lease_expires_at - as_of).total_seconds())
        if connection is not None
        else None
    )
    return {
        "metric_window_seconds": METRIC_WINDOW.total_seconds(),
        "recent_requests": recent_inbound.count(),
        "recent_completed_requests": completed,
        "processing_rate_per_minute": completed / (METRIC_WINDOW.total_seconds() / 60),
        "recent_request_errors": request_errors,
        "recent_outbound_errors": outbound_errors,
        "recent_retry_attempts": retry_attempts,
        "inbound_processing": inbound_processing,
        "outbound_pressure": outbound_pressure,
        "pending_work": inbound_processing + outbound_pressure,
        "oldest_pending_at": _iso(oldest_pending),
        "oldest_pending_age_seconds": _age(as_of, oldest_pending),
        "connection_live": bool(connection and connection.lease_expires_at >= as_of),
        "connection_lease_remaining_seconds": lease_remaining,
    }


def _age(current: datetime, value: datetime | None) -> float | None:
    if value is None:
        return None
    return max(0.0, (current - value).total_seconds())


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None

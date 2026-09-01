from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.cards.models import RFID, RFIDAttempt, RFIDWatchlistEntry, RFIDWatchlistEvent
from apps.features.utils import get_cached_feature_enabled

logger = logging.getLogger(__name__)

RFID_WATCHLISTS_FEATURE_SLUG = "rfid-watchlists"
RFID_WATCHLISTS_FEATURE_CACHE_KEY = "feature-enabled:rfid-watchlists"


def rfid_watchlists_enabled() -> bool:
    """Return whether RFID watchlist evaluation is enabled."""

    default = bool(getattr(settings, "RFID_WATCHLISTS_ENABLED", True))
    return get_cached_feature_enabled(
        RFID_WATCHLISTS_FEATURE_SLUG,
        cache_key=RFID_WATCHLISTS_FEATURE_CACHE_KEY,
        timeout=300,
        default=default,
    )


def _event_idempotency_key(entry: RFIDWatchlistEntry, attempt: RFIDAttempt) -> str:
    return f"rfid-watchlist:{entry.pk}:attempt:{attempt.pk}"


def _candidate_rfid_values(normalized: str) -> set[str]:
    if not normalized:
        return set()
    values = {normalized}
    reversed_uid = RFID.reverse_uid(normalized)
    if reversed_uid and reversed_uid != normalized:
        values.add(reversed_uid)
    return values


def matching_watchlist_entries(attempt: RFIDAttempt) -> Iterable[RFIDWatchlistEntry]:
    """Yield enabled watchlist entries matching the recorded RFID attempt."""

    normalized = RFID.normalize_code(attempt.rfid)
    if not normalized:
        return RFIDWatchlistEntry.objects.none()
    candidate_values = _candidate_rfid_values(normalized)
    query = Q(normalized_rfid__in=candidate_values) | Q(
        label__rfid__in=candidate_values
    )
    if attempt.label_id:
        query |= Q(label_id=attempt.label_id)
    return RFIDWatchlistEntry.objects.filter(enabled=True).filter(query).distinct()


def _create_event(
    entry: RFIDWatchlistEntry,
    attempt: RFIDAttempt,
    *,
    status: str,
    action_error: str = "",
) -> RFIDWatchlistEvent | None:
    payload = {
        "attempt_id": attempt.pk,
        "attempt_status": attempt.status,
        "authenticated": attempt.authenticated,
        "allowed": attempt.allowed,
        "charger_id": attempt.charger_id,
        "account_id": attempt.account_id,
        "transaction_id": attempt.transaction_id,
    }
    try:
        event, created = RFIDWatchlistEvent.objects.get_or_create(
            idempotency_key=_event_idempotency_key(entry, attempt),
            defaults={
                "entry": entry,
                "attempt": attempt,
                "label_id": attempt.label_id or entry.label_id,
                "rfid": RFID.normalize_code(attempt.rfid),
                "source": attempt.source,
                "status": status,
                "match_payload": payload,
                "action_error": action_error,
            },
        )
    except IntegrityError:
        return None
    return event if created else None


def _mark_queue_error(event_id: int) -> None:
    RFIDWatchlistEvent.objects.filter(
        pk=event_id,
        status=RFIDWatchlistEvent.Status.PENDING,
    ).update(queue_error="Celery enqueue unavailable")


def _enqueue_event_id(event_id: int) -> bool:
    from apps.cards.tasks import process_rfid_watchlist_event
    from apps.celery.utils import enqueue_task

    queued = enqueue_task(process_rfid_watchlist_event, event_id)
    if not queued:
        _mark_queue_error(event_id)
    return queued


def record_watchlist_events_for_attempt(attempt: RFIDAttempt) -> list[RFIDWatchlistEvent]:
    """Create durable watchlist events for a recorded RFID attempt."""

    if not rfid_watchlists_enabled():
        return []
    if not attempt.pk or not RFID.normalize_code(attempt.rfid):
        return []

    now = timezone.now()
    pending_event_ids: list[int] = []
    events: list[RFIDWatchlistEvent] = []

    for entry in matching_watchlist_entries(attempt):
        if entry.is_rate_limited(now):
            event = _create_event(
                entry,
                attempt,
                status=RFIDWatchlistEvent.Status.RATE_LIMITED,
                action_error="Rate limit active",
            )
            if event is not None:
                events.append(event)
            continue

        event = _create_event(
            entry,
            attempt,
            status=RFIDWatchlistEvent.Status.PENDING,
        )
        if event is None:
            continue
        events.append(event)
        pending_event_ids.append(event.pk)
        RFIDWatchlistEntry.objects.filter(pk=entry.pk).update(last_matched_at=now)
        entry.last_matched_at = now

    if pending_event_ids:
        transaction.on_commit(
            lambda event_ids=tuple(pending_event_ids): [
                _enqueue_event_id(event_id) for event_id in event_ids
            ]
        )
    return events


def _action_text(config: dict[str, Any], key: str, fallback: str, max_length: int) -> str:
    value = str(config.get(key) or fallback).strip()
    return value[:max_length]


def _process_audit_event(event: RFIDWatchlistEvent) -> str:
    return f"audit:{event.rfid}:{event.source or 'unknown'}"


def _process_local_notification_event(event: RFIDWatchlistEvent) -> str:
    from apps.core.notifications import notify

    config = event.entry.action_config if isinstance(event.entry.action_config, dict) else {}
    subject = _action_text(config, "subject", "RFID watchlist", 64)
    body = _action_text(config, "body", event.rfid, 160)
    notify(subject, body)
    return "local-notification"


def _process_net_message_event(event: RFIDWatchlistEvent) -> str:
    from apps.nodes.models import NetMessage

    config = event.entry.action_config if isinstance(event.entry.action_config, dict) else {}
    subject = _action_text(config, "subject", "RFID watchlist", 64)
    body = _action_text(config, "body", event.rfid, 256)
    NetMessage.broadcast(
        subject=subject,
        body=body,
        reach=config.get("reach") or None,
    )
    return "net-message"


ACTION_HANDLERS = {
    RFIDWatchlistEntry.ActionType.AUDIT: _process_audit_event,
    RFIDWatchlistEntry.ActionType.LOCAL_NOTIFICATION: _process_local_notification_event,
    RFIDWatchlistEntry.ActionType.NET_MESSAGE: _process_net_message_event,
}


def process_watchlist_event(event_id: int) -> str:
    """Deliver one pending watchlist event through its allowlisted action."""

    event = (
        RFIDWatchlistEvent.objects.select_related("entry")
        .filter(pk=event_id)
        .first()
    )
    if event is None:
        return "missing"
    if event.status != RFIDWatchlistEvent.Status.PENDING:
        return event.status
    if not event.entry.enabled:
        event.status = RFIDWatchlistEvent.Status.SUPPRESSED
        event.action_error = "Watchlist entry disabled"
        event.processed_at = timezone.now()
        event.save(update_fields=["status", "action_error", "processed_at"])
        return event.status

    handler = ACTION_HANDLERS.get(event.entry.action_type)
    if handler is None:
        event.retry_count += 1
        event.status = RFIDWatchlistEvent.Status.FAILED
        event.action_error = f"Unsupported action type: {event.entry.action_type}"
        event.processed_at = timezone.now()
        event.save(
            update_fields=["retry_count", "status", "action_error", "processed_at"]
        )
        return event.status

    try:
        output = handler(event)
    except Exception as exc:
        logger.warning("RFID watchlist action failed", exc_info=True)
        event.retry_count += 1
        event.action_error = str(exc)
        if event.retry_count >= max(1, int(event.entry.max_retries or 1)):
            event.status = RFIDWatchlistEvent.Status.FAILED
            event.processed_at = timezone.now()
            update_fields = [
                "retry_count",
                "action_error",
                "status",
                "processed_at",
            ]
        else:
            update_fields = ["retry_count", "action_error"]
        event.save(update_fields=update_fields)
        if event.status == RFIDWatchlistEvent.Status.PENDING:
            transaction.on_commit(lambda event_id=event.pk: _enqueue_event_id(event_id))
        return event.status

    event.status = RFIDWatchlistEvent.Status.DELIVERED
    event.action_output = str(output or "")[:2000]
    event.action_error = ""
    event.queue_error = ""
    event.processed_at = timezone.now()
    event.save(
        update_fields=[
            "status",
            "action_output",
            "action_error",
            "queue_error",
            "processed_at",
        ]
    )
    return event.status


def process_pending_watchlist_events(limit: int = 100) -> int:
    """Process pending watchlist events and return the number attempted."""

    event_ids = list(
        RFIDWatchlistEvent.objects.filter(status=RFIDWatchlistEvent.Status.PENDING)
        .order_by("created_at", "pk")
        .values_list("pk", flat=True)[: max(1, int(limit or 1))]
    )
    for event_id in event_ids:
        process_watchlist_event(event_id)
    return len(event_ids)

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

import apps.cards.watchlists as watchlists
from apps.cards.models import (
    RFID,
    RFIDAttempt,
    RFIDWatchlistEntry,
    RFIDWatchlistEvent,
)

pytestmark = pytest.mark.django_db


def _record_attempt(
    rfid: str = "ABCD1234", *, source: str = RFIDAttempt.Source.SERVICE
):
    return RFIDAttempt.record_attempt(
        {"rfid": rfid, "service_mode": "service"},
        source=source,
        status=RFIDAttempt.Status.SCANNED,
    )


def test_watchlist_entry_creates_pending_event_and_enqueue(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    enqueued: list[int] = []
    monkeypatch.setattr(
        watchlists,
        "_enqueue_event_id",
        lambda event_id: enqueued.append(event_id) or True,
    )
    RFIDWatchlistEntry.objects.create(
        normalized_rfid="abcd1234",
        name="Front desk",
        action_type=RFIDWatchlistEntry.ActionType.AUDIT,
    )

    with django_capture_on_commit_callbacks(execute=True):
        attempt = _record_attempt()

    event = RFIDWatchlistEvent.objects.get()
    assert event.attempt == attempt
    assert event.rfid == "ABCD1234"
    assert event.source == RFIDAttempt.Source.SERVICE
    assert event.status == RFIDWatchlistEvent.Status.PENDING
    assert enqueued == [event.pk]


def test_watchlist_collects_action_config_validation_errors():
    entry = RFIDWatchlistEntry(
        normalized_rfid="ABCD1234",
        action_type=RFIDWatchlistEntry.ActionType.LOCAL_NOTIFICATION,
        action_config={
            "extra": "value",
        },
    )

    with pytest.raises(ValidationError) as exc_info:
        entry.full_clean()

    action_config_errors = exc_info.value.message_dict["action_config"]
    assert len(action_config_errors) == 1
    assert "Unsupported action config keys: extra" in action_config_errors


def test_watchlist_no_match_creates_no_event(monkeypatch):
    monkeypatch.setattr(watchlists, "_enqueue_event_id", lambda event_id: True)
    RFIDWatchlistEntry.objects.create(normalized_rfid="DEADBEEF")

    _record_attempt("ABCD1234")

    assert RFIDWatchlistEvent.objects.count() == 0


def test_watchlist_disabled_entry_creates_no_event(monkeypatch):
    monkeypatch.setattr(watchlists, "_enqueue_event_id", lambda event_id: True)
    RFIDWatchlistEntry.objects.create(normalized_rfid="ABCD1234", enabled=False)

    _record_attempt("ABCD1234")

    assert RFIDWatchlistEvent.objects.count() == 0


def test_watchlist_rate_limit_records_suppressed_event(monkeypatch):
    enqueued: list[int] = []
    monkeypatch.setattr(
        watchlists,
        "_enqueue_event_id",
        lambda event_id: enqueued.append(event_id) or True,
    )
    RFIDWatchlistEntry.objects.create(
        normalized_rfid="ABCD1234",
        last_matched_at=timezone.now() - timedelta(seconds=5),
        rate_limit_seconds=60,
    )

    _record_attempt("ABCD1234")

    event = RFIDWatchlistEvent.objects.get()
    assert event.status == RFIDWatchlistEvent.Status.RATE_LIMITED
    assert event.action_error == "Rate limit active"
    assert enqueued == []


def test_watchlist_queue_failure_keeps_pending_event(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    def fail_enqueue(event_id):
        watchlists._mark_queue_error(event_id)
        return False

    monkeypatch.setattr(watchlists, "_enqueue_event_id", fail_enqueue)
    RFIDWatchlistEntry.objects.create(normalized_rfid="ABCD1234")

    with django_capture_on_commit_callbacks(execute=True):
        _record_attempt("ABCD1234")

    event = RFIDWatchlistEvent.objects.get()
    assert event.status == RFIDWatchlistEvent.Status.PENDING
    assert event.queue_error == "Celery enqueue unavailable"


def test_watchlist_duplicate_create_race_does_not_enqueue(monkeypatch):
    entry = RFIDWatchlistEntry.objects.create(normalized_rfid="ABCD1234")
    attempt = RFIDAttempt.objects.create(
        rfid="ABCD1234",
        source=RFIDAttempt.Source.SERVICE,
    )

    def raise_integrity_error(*args, **kwargs):
        raise IntegrityError("duplicate")

    monkeypatch.setattr(
        RFIDWatchlistEvent.objects,
        "get_or_create",
        raise_integrity_error,
    )

    event = watchlists._create_event(
        entry,
        attempt,
        status=RFIDWatchlistEvent.Status.PENDING,
    )

    assert event is None


def test_watchlist_retryable_action_failure_requeues(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    entry = RFIDWatchlistEntry.objects.create(
        normalized_rfid="ABCD1234",
        action_type=RFIDWatchlistEntry.ActionType.AUDIT,
        max_retries=2,
    )
    event = RFIDWatchlistEvent.objects.create(
        entry=entry,
        rfid="ABCD1234",
        source=RFIDAttempt.Source.SERVICE,
        idempotency_key="manual-retryable-action-failure",
    )
    enqueued: list[int] = []

    def fail_action(event):
        raise RuntimeError("temporary outage")

    monkeypatch.setitem(
        watchlists.ACTION_HANDLERS,
        RFIDWatchlistEntry.ActionType.AUDIT,
        fail_action,
    )
    monkeypatch.setattr(
        watchlists,
        "_enqueue_event_id",
        lambda event_id: enqueued.append(event_id) or True,
    )

    with django_capture_on_commit_callbacks(execute=True):
        result = watchlists.process_watchlist_event(event.pk)

    event.refresh_from_db()
    assert result == RFIDWatchlistEvent.Status.PENDING
    assert event.status == RFIDWatchlistEvent.Status.PENDING
    assert event.retry_count == 1
    assert "temporary outage" in event.action_error
    assert enqueued == [event.pk]


def test_watchlist_action_failure_obeys_retry_limit(monkeypatch):
    entry = RFIDWatchlistEntry.objects.create(
        normalized_rfid="ABCD1234",
        action_type=RFIDWatchlistEntry.ActionType.NET_MESSAGE,
        max_retries=1,
    )
    event = RFIDWatchlistEvent.objects.create(
        entry=entry,
        rfid="ABCD1234",
        source=RFIDAttempt.Source.SERVICE,
        idempotency_key="manual-action-failure",
    )

    def fail_broadcast(**kwargs):
        raise RuntimeError("message bus down")

    monkeypatch.setattr("apps.nodes.models.NetMessage.broadcast", fail_broadcast)

    result = watchlists.process_watchlist_event(event.pk)

    event.refresh_from_db()
    assert result == RFIDWatchlistEvent.Status.FAILED
    assert event.retry_count == 1
    assert event.status == RFIDWatchlistEvent.Status.FAILED
    assert "message bus down" in event.action_error


def test_watchlist_audit_action_delivers_event():
    entry = RFIDWatchlistEntry.objects.create(
        normalized_rfid="ABCD1234",
        action_type=RFIDWatchlistEntry.ActionType.AUDIT,
    )
    event = RFIDWatchlistEvent.objects.create(
        entry=entry,
        rfid="ABCD1234",
        source=RFIDAttempt.Source.SERVICE,
        idempotency_key="manual-audit",
    )

    result = watchlists.process_watchlist_event(event.pk)

    event.refresh_from_db()
    assert result == RFIDWatchlistEvent.Status.DELIVERED
    assert event.action_output == "audit:ABCD1234:service"


def test_watchlist_can_match_recorded_label(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    enqueued: list[int] = []
    monkeypatch.setattr(
        watchlists,
        "_enqueue_event_id",
        lambda event_id: enqueued.append(event_id) or True,
    )
    tag = RFID.objects.create(rfid="AABBCCDD")
    RFIDWatchlistEntry.objects.create(label=tag)

    with django_capture_on_commit_callbacks(execute=True):
        RFIDAttempt.record_attempt(
            {"rfid": "AABBCCDD", "label_id": tag.pk},
            source=RFIDAttempt.Source.SERVICE,
        )

    event = RFIDWatchlistEvent.objects.get()
    assert event.label == tag
    assert enqueued == [event.pk]


def test_watchlist_can_match_label_by_recorded_rfid(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    enqueued: list[int] = []
    monkeypatch.setattr(
        watchlists,
        "_enqueue_event_id",
        lambda event_id: enqueued.append(event_id) or True,
    )
    tag = RFID.objects.create(rfid="AABBCCDD")
    RFIDWatchlistEntry.objects.create(label=tag)

    with django_capture_on_commit_callbacks(execute=True):
        RFIDAttempt.record_attempt(
            {"rfid": "AABBCCDD"},
            source=RFIDAttempt.Source.SERVICE,
        )

    event = RFIDWatchlistEvent.objects.get()
    assert event.label == tag
    assert enqueued == [event.pk]

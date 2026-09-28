from datetime import datetime, timedelta, timezone

import pytest

from apps.events.models import EventEnvelope
from apps.ocpp.models import (
    ChargerConnection,
    ChargerTimelineProgress,
    InboundProtocolRequest,
    ProtocolOperation,
)
from apps.ocpp.services.timeline import observe_timeline
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from apps.ocpp.services.timeline_status import (
    query_timeline_status,
    timeline_snapshot,
    timeline_status,
)
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db(transaction=True)


def test_snapshot_is_json_safe_and_read_only():
    selected = charger("timeline-status")
    received = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    progress = observe_timeline(
        charger=selected,
        event_at=received - timedelta(hours=2),
        received_at=received,
    )

    assert timeline_snapshot(progress) == {
        "charger_id": selected.pk,
        "charger_identity": selected.identity,
        "state": ChargerTimelineProgress.State.HISTORICAL,
        "condition": "catching_up",
        "as_of": received.isoformat(),
        "newest_event_at": (received - timedelta(hours=2)).isoformat(),
        "last_received_at": received.isoformat(),
        "event_lag_seconds": 7200.0,
        "receipt_age_seconds": 0.0,
        "historical_events_seen": 1,
        "observed_events": 1,
        "metric_window_seconds": 300.0,
        "recent_requests": 0,
        "recent_completed_requests": 0,
        "processing_rate_per_minute": 0.0,
        "mean_processing_latency_seconds": None,
        "max_processing_latency_seconds": None,
        "recent_request_errors": 0,
        "recent_outbound_errors": 0,
        "recent_retry_attempts": 0,
        "inbound_processing": 0,
        "outbound_pressure": 0,
        "pending_work": 0,
        "oldest_pending_at": None,
        "oldest_pending_age_seconds": None,
        "last_authorization_at": None,
        "last_authorization_age_seconds": None,
        "last_authorization_status": None,
        "connection_live": False,
        "connection_lease_remaining_seconds": None,
    }


def test_state_transition_publishes_operator_event_after_commit():
    selected = charger("timeline-transition")
    received = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)

    observe_timeline(
        charger=selected,
        event_at=received - timedelta(days=30),
        received_at=received,
    )

    event = EventEnvelope.objects.get(
        event_type="ocpp.timeline.state_changed",
        producer="ocpp.timeline",
    )
    assert event.payload["charger_id"] == selected.pk
    assert event.payload["previous_state"] == ChargerTimelineProgress.State.UNKNOWN
    assert event.payload["state"] == ChargerTimelineProgress.State.HISTORICAL
    assert event.payload["condition"] == "catching_up"


def test_same_state_does_not_emit_duplicate_transition_event():
    selected = charger("timeline-stable")
    received = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)

    observe_timeline(
        charger=selected,
        event_at=received - timedelta(minutes=2),
        received_at=received,
    )
    observe_timeline(
        charger=selected,
        event_at=received - timedelta(minutes=1),
        received_at=received + timedelta(seconds=1),
    )

    assert EventEnvelope.objects.filter(
        event_type="ocpp.timeline.state_changed",
        producer="ocpp.timeline",
    ).count() == 1



def test_query_without_timeline_data_returns_unknown_without_creating_state():
    selected = charger("timeline-no-data")
    current = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)

    before = ChargerTimelineProgress.objects.count()
    snapshot = timeline_status(selected, now=current)

    assert snapshot == {
        "charger_id": selected.pk,
        "charger_identity": selected.identity,
        "state": ChargerTimelineProgress.State.UNKNOWN,
        "condition": "unknown",
        "as_of": current.isoformat(),
        "newest_event_at": None,
        "last_received_at": None,
        "event_lag_seconds": None,
        "receipt_age_seconds": None,
        "historical_events_seen": 0,
        "observed_events": 0,
        "metric_window_seconds": 300.0,
        "recent_requests": 0,
        "recent_completed_requests": 0,
        "processing_rate_per_minute": 0.0,
        "recent_request_errors": 0,
        "recent_outbound_errors": 0,
        "recent_retry_attempts": 0,
        "inbound_processing": 0,
        "outbound_pressure": 0,
        "connection_live": False,
        "connection_lease_remaining_seconds": None,
    }
    assert ChargerTimelineProgress.objects.count() == before


def test_query_reports_lag_and_receipt_age_without_mutating_progress():
    selected = charger("timeline-query")
    received = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    progress = observe_timeline(
        charger=selected,
        event_at=received - timedelta(minutes=20),
        received_at=received,
    )
    updated_at = progress.updated_at

    snapshot = timeline_status(
        selected,
        now=received + timedelta(minutes=5),
    )

    assert snapshot["event_lag_seconds"] == 1500.0
    assert snapshot["receipt_age_seconds"] == 300.0
    progress.refresh_from_db()
    assert progress.updated_at == updated_at



def test_query_surface_resolves_charger_by_public_identity():
    selected = charger("timeline-public-query")
    current = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)

    snapshot = query_timeline_status(selected.identity, now=current)

    assert snapshot["charger_id"] == selected.pk
    assert snapshot["charger_identity"] == selected.identity
    assert snapshot["state"] == ChargerTimelineProgress.State.UNKNOWN



@pytest.mark.parametrize(
    ("state", "age", "expected"),
    [
        (ChargerTimelineProgress.State.LIVE, timedelta(minutes=15), "ready"),
        (ChargerTimelineProgress.State.LIVE, timedelta(minutes=15, seconds=1), "degraded"),
        (ChargerTimelineProgress.State.HISTORICAL, timedelta(minutes=15), "catching_up"),
        (ChargerTimelineProgress.State.HISTORICAL, timedelta(minutes=15, seconds=1), "stalled"),
        (ChargerTimelineProgress.State.CATCHING_UP, timedelta(minutes=15), "catching_up"),
        (ChargerTimelineProgress.State.CATCHING_UP, timedelta(minutes=15, seconds=1), "stalled"),
    ],
)
def test_operator_condition_boundaries(state, age, expected):
    selected = charger(f"timeline-condition-{state}-{expected}")
    current = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    progress = ChargerTimelineProgress.objects.create(
        charger=selected,
        state=state,
        newest_event_at=current - timedelta(minutes=2),
        last_received_at=current - age,
        observed_events=2,
        historical_events_seen=2,
    )

    assert timeline_snapshot(progress, as_of=current)["condition"] == expected


def test_unknown_state_remains_unknown_even_with_recent_receipt():
    selected = charger("timeline-condition-unknown")
    current = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    progress = ChargerTimelineProgress.objects.create(
        charger=selected,
        state=ChargerTimelineProgress.State.UNKNOWN,
        last_received_at=current,
        observed_events=1,
    )

    assert timeline_snapshot(progress, as_of=current)["condition"] == "unknown"


def _inbound_request(
    selected,
    *,
    unique_id: str,
    status: str,
    response_kind: str = "",
    action: str = "Heartbeat",
    response_payload=None,
):
    return InboundProtocolRequest.objects.create(
        charger=selected,
        version=ProtocolVersion.OCPP_16.value,
        direction=Direction.CHARGE_POINT_TO_CSMS.value,
        action=action,
        unique_id=unique_id,
        fingerprint=unique_id,
        identity_key=unique_id,
        status=status,
        response_kind=response_kind,
        response_payload=response_payload,
    )


def test_snapshot_reports_recent_rate_errors_retries_and_pressure():
    selected = charger("timeline-metrics")
    current = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    progress = ChargerTimelineProgress.objects.create(
        charger=selected,
        state=ChargerTimelineProgress.State.CATCHING_UP,
        newest_event_at=current - timedelta(hours=1),
        last_received_at=current,
        observed_events=4,
        historical_events_seen=4,
    )

    completed = _inbound_request(
        selected,
        unique_id="recent-ok",
        status=InboundProtocolRequest.Status.COMPLETED,
    )
    errored = _inbound_request(
        selected,
        unique_id="recent-error",
        status=InboundProtocolRequest.Status.COMPLETED,
        response_kind=InboundProtocolRequest.ResponseKind.ERROR,
    )
    processing = _inbound_request(
        selected,
        unique_id="processing",
        status=InboundProtocolRequest.Status.PROCESSING,
    )
    old = _inbound_request(
        selected,
        unique_id="old",
        status=InboundProtocolRequest.Status.COMPLETED,
    )
    InboundProtocolRequest.objects.filter(pk__in=[completed.pk, errored.pk, processing.pk]).update(
        received_at=current - timedelta(minutes=1)
    )
    InboundProtocolRequest.objects.filter(pk=completed.pk).update(
        completed_at=current - timedelta(minutes=1) + timedelta(seconds=2)
    )
    InboundProtocolRequest.objects.filter(pk=errored.pk).update(
        completed_at=current - timedelta(minutes=1) + timedelta(seconds=4)
    )
    InboundProtocolRequest.objects.filter(pk=old.pk).update(
        received_at=current - timedelta(minutes=10)
    )

    queued = ProtocolOperation.objects.create(
        charger=selected,
        version=ProtocolVersion.OCPP_16.value,
        direction=ProtocolOperation.Direction.CSMS_TO_CHARGE_POINT,
        action="Reset",
        status=ProtocolOperation.Status.PENDING,
        attempt_count=0,
    )
    retried = ProtocolOperation.objects.create(
        charger=selected,
        version=ProtocolVersion.OCPP_16.value,
        direction=ProtocolOperation.Direction.CSMS_TO_CHARGE_POINT,
        action="Reset",
        status=ProtocolOperation.Status.ERRORED,
        attempt_count=3,
    )
    ProtocolOperation.objects.filter(pk__in=[queued.pk, retried.pk]).update(
        created_at=current - timedelta(minutes=1)
    )

    ChargerConnection.objects.create(
        charger=selected,
        channel_name="metrics-channel",
        protocol=ProtocolVersion.OCPP_16.value,
        last_seen_at=current,
        lease_expires_at=current + timedelta(seconds=90),
    )

    snapshot = timeline_snapshot(progress, as_of=current)

    assert snapshot["recent_requests"] == 3
    assert snapshot["recent_completed_requests"] == 2
    assert snapshot["processing_rate_per_minute"] == pytest.approx(0.4)
    assert snapshot["mean_processing_latency_seconds"] == 3.0
    assert snapshot["max_processing_latency_seconds"] == 4.0
    assert snapshot["recent_request_errors"] == 1
    assert snapshot["recent_outbound_errors"] == 1
    assert snapshot["recent_retry_attempts"] == 2
    assert snapshot["inbound_processing"] == 1
    assert snapshot["outbound_pressure"] == 1
    assert snapshot["pending_work"] == 2
    assert snapshot["oldest_pending_at"] == (current - timedelta(minutes=1)).isoformat()
    assert snapshot["oldest_pending_age_seconds"] == 60.0
    assert snapshot["connection_live"] is True
    assert snapshot["connection_lease_remaining_seconds"] == 90.0


def test_historical_snapshot_excludes_future_metric_records():
    selected = charger("timeline-metrics-as-of")
    current = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    progress = ChargerTimelineProgress.objects.create(
        charger=selected,
        state=ChargerTimelineProgress.State.LIVE,
        newest_event_at=current,
        last_received_at=current,
    )
    future = _inbound_request(
        selected,
        unique_id="future",
        status=InboundProtocolRequest.Status.COMPLETED,
    )
    InboundProtocolRequest.objects.filter(pk=future.pk).update(
        received_at=current + timedelta(minutes=1)
    )

    snapshot = timeline_snapshot(progress, as_of=current)

    assert snapshot["recent_requests"] == 0
    assert snapshot["recent_completed_requests"] == 0


def test_pending_work_uses_oldest_authoritative_timestamp():
    selected = charger("timeline-pending-oldest")
    current = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    progress = ChargerTimelineProgress.objects.create(
        charger=selected,
        state=ChargerTimelineProgress.State.CATCHING_UP,
        newest_event_at=current - timedelta(hours=1),
        last_received_at=current,
    )
    inbound = _inbound_request(
        selected,
        unique_id="pending-inbound",
        status=InboundProtocolRequest.Status.PROCESSING,
    )
    InboundProtocolRequest.objects.filter(pk=inbound.pk).update(
        received_at=current - timedelta(minutes=4)
    )
    outbound = ProtocolOperation.objects.create(
        charger=selected,
        version=ProtocolVersion.OCPP_16.value,
        direction=ProtocolOperation.Direction.CSMS_TO_CHARGE_POINT,
        action="Reset",
        status=ProtocolOperation.Status.RECOVERY_REQUIRED,
    )
    ProtocolOperation.objects.filter(pk=outbound.pk).update(
        created_at=current - timedelta(minutes=2)
    )

    snapshot = timeline_snapshot(progress, as_of=current)

    assert snapshot["pending_work"] == 2
    assert snapshot["oldest_pending_at"] == (current - timedelta(minutes=4)).isoformat()
    assert snapshot["oldest_pending_age_seconds"] == 240.0


def test_processing_latency_ignores_incomplete_and_future_completions():
    selected = charger("timeline-latency-as-of")
    current = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    progress = ChargerTimelineProgress.objects.create(
        charger=selected,
        state=ChargerTimelineProgress.State.LIVE,
        newest_event_at=current,
        last_received_at=current,
    )
    complete = _inbound_request(
        selected,
        unique_id="latency-complete",
        status=InboundProtocolRequest.Status.COMPLETED,
    )
    future = _inbound_request(
        selected,
        unique_id="latency-future",
        status=InboundProtocolRequest.Status.COMPLETED,
    )
    incomplete = _inbound_request(
        selected,
        unique_id="latency-incomplete",
        status=InboundProtocolRequest.Status.COMPLETED,
    )
    for request in (complete, future, incomplete):
        InboundProtocolRequest.objects.filter(pk=request.pk).update(
            received_at=current - timedelta(seconds=10)
        )
    InboundProtocolRequest.objects.filter(pk=complete.pk).update(
        completed_at=current - timedelta(seconds=4)
    )
    InboundProtocolRequest.objects.filter(pk=future.pk).update(
        completed_at=current + timedelta(seconds=1)
    )

    snapshot = timeline_snapshot(progress, as_of=current)

    assert snapshot["recent_completed_requests"] == 1
    assert snapshot["mean_processing_latency_seconds"] == 6.0
    assert snapshot["max_processing_latency_seconds"] == 6.0


def test_snapshot_reports_latest_authorization_activity_without_exposing_id_tag():
    selected = charger("timeline-authorization")
    current = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    progress = ChargerTimelineProgress.objects.create(
        charger=selected,
        state=ChargerTimelineProgress.State.LIVE,
        newest_event_at=current,
        last_received_at=current,
    )

    older = _inbound_request(
        selected,
        unique_id="authorize-old",
        action="Authorize",
        status=InboundProtocolRequest.Status.COMPLETED,
        response_kind=InboundProtocolRequest.ResponseKind.RESULT,
        response_payload={"idTagInfo": {"status": "Invalid"}},
    )
    latest = _inbound_request(
        selected,
        unique_id="authorize-new",
        action="Authorize",
        status=InboundProtocolRequest.Status.COMPLETED,
        response_kind=InboundProtocolRequest.ResponseKind.RESULT,
        response_payload={"idTagInfo": {"status": "Accepted"}},
    )
    InboundProtocolRequest.objects.filter(pk=older.pk).update(
        received_at=current - timedelta(minutes=4),
        completed_at=current - timedelta(minutes=4) + timedelta(seconds=1),
    )
    InboundProtocolRequest.objects.filter(pk=latest.pk).update(
        received_at=current - timedelta(seconds=45),
        completed_at=current - timedelta(seconds=44),
    )

    snapshot = timeline_snapshot(progress, as_of=current)

    assert snapshot["last_authorization_at"] == (
        current - timedelta(seconds=45)
    ).isoformat()
    assert snapshot["last_authorization_age_seconds"] == 45.0
    assert snapshot["last_authorization_status"] == "Accepted"
    assert "idTag" not in snapshot
    assert "presented_id" not in snapshot


def test_authorization_activity_reports_processing_and_excludes_future_requests():
    selected = charger("timeline-authorization-as-of")
    current = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    progress = ChargerTimelineProgress.objects.create(
        charger=selected,
        state=ChargerTimelineProgress.State.LIVE,
        newest_event_at=current,
        last_received_at=current,
    )

    processing = _inbound_request(
        selected,
        unique_id="authorize-processing",
        action="Authorize",
        status=InboundProtocolRequest.Status.PROCESSING,
    )
    future = _inbound_request(
        selected,
        unique_id="authorize-future",
        action="Authorize",
        status=InboundProtocolRequest.Status.COMPLETED,
        response_kind=InboundProtocolRequest.ResponseKind.RESULT,
        response_payload={"idTagInfo": {"status": "Invalid"}},
    )
    InboundProtocolRequest.objects.filter(pk=processing.pk).update(
        received_at=current - timedelta(seconds=30)
    )
    InboundProtocolRequest.objects.filter(pk=future.pk).update(
        received_at=current + timedelta(seconds=1),
        completed_at=current + timedelta(seconds=2),
    )

    snapshot = timeline_snapshot(progress, as_of=current)

    assert snapshot["last_authorization_at"] == (
        current - timedelta(seconds=30)
    ).isoformat()
    assert snapshot["last_authorization_status"] == InboundProtocolRequest.Status.PROCESSING

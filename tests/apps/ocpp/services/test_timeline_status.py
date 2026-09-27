from datetime import datetime, timedelta, timezone

import pytest

from apps.events.models import EventEnvelope
from apps.ocpp.models import ChargerTimelineProgress
from apps.ocpp.services.timeline import observe_timeline
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
        "as_of": received.isoformat(),
        "newest_event_at": (received - timedelta(hours=2)).isoformat(),
        "last_received_at": received.isoformat(),
        "event_lag_seconds": 7200.0,
        "receipt_age_seconds": 0.0,
        "historical_events_seen": 1,
        "observed_events": 1,
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
        "as_of": current.isoformat(),
        "newest_event_at": None,
        "last_received_at": None,
        "event_lag_seconds": None,
        "receipt_age_seconds": None,
        "historical_events_seen": 0,
        "observed_events": 0,
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

from datetime import datetime, timedelta, timezone

import pytest

from apps.events.models import EventEnvelope
from apps.ocpp.models import ChargerTimelineProgress
from apps.ocpp.services.timeline import observe_timeline
from apps.ocpp.services.timeline_status import timeline_snapshot
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
        "state": ChargerTimelineProgress.State.HISTORICAL,
        "newest_event_at": (received - timedelta(hours=2)).isoformat(),
        "last_received_at": received.isoformat(),
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
    event = received - timedelta(days=30)

    observe_timeline(charger=selected, event_at=event, received_at=received)
    observe_timeline(
        charger=selected,
        event_at=event + timedelta(days=1),
        received_at=received + timedelta(seconds=1),
    )

    assert EventEnvelope.objects.filter(
        event_type="ocpp.timeline.state_changed",
        producer="ocpp.timeline",
    ).count() == 2

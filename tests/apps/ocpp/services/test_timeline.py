from datetime import datetime, timedelta, timezone

import pytest

from apps.ocpp.models import ChargerTimelineProgress
from apps.ocpp.services.timeline import observe_timeline
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


def test_historical_stream_records_receipt_and_event_clocks_separately():
    selected = charger("timeline-history")
    received = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    event = datetime(2023, 1, 1, 8, tzinfo=timezone.utc)

    first = observe_timeline(charger=selected, event_at=event, received_at=received)
    second = observe_timeline(
        charger=selected,
        event_at=event + timedelta(days=30),
        received_at=received + timedelta(seconds=1),
    )

    assert first.state == ChargerTimelineProgress.State.HISTORICAL
    assert second.state == ChargerTimelineProgress.State.CATCHING_UP
    assert second.newest_event_at == event + timedelta(days=30)
    assert second.last_received_at == received + timedelta(seconds=1)
    assert second.historical_events_seen == 2
    assert second.observed_events == 2


def test_live_event_classifies_timeline_live_without_queue_percentage():
    selected = charger("timeline-live")
    received = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)

    progress = observe_timeline(
        charger=selected,
        event_at=received - timedelta(minutes=2),
        received_at=received,
    )

    assert progress.state == ChargerTimelineProgress.State.LIVE
    assert not hasattr(progress, "remaining_events")
    assert not hasattr(progress, "percent_complete")


def test_missing_event_timestamp_keeps_transport_observation_without_guessing():
    selected = charger("timeline-unknown")
    received = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)

    progress = observe_timeline(charger=selected, event_at=None, received_at=received)

    assert progress.state == ChargerTimelineProgress.State.UNKNOWN
    assert progress.last_received_at == received
    assert progress.newest_event_at is None
    assert progress.observed_events == 1

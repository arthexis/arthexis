from datetime import datetime, timedelta, timezone

import pytest

from apps.ocpp.models import ChargerTimelineProgress
from apps.ocpp.services import display_status
from apps.ocpp.services.display_status import DISPLAY_STATUS_FIELDS, query_display_status
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db(transaction=True)


def test_display_projection_is_compact_and_read_only():
    selected = charger("display-health")
    current = datetime(2026, 9, 28, 2, tzinfo=timezone.utc)
    ChargerTimelineProgress.objects.create(
        charger=selected,
        state=ChargerTimelineProgress.State.CATCHING_UP,
        newest_event_at=current - timedelta(hours=1),
        last_received_at=current,
        observed_events=8,
        historical_events_seen=8,
    )

    before = ChargerTimelineProgress.objects.count()
    payload = query_display_status(selected.identity)

    assert payload["charger"] == selected.identity
    assert payload["condition"] in {"catching_up", "stalled"}
    assert "pending_work" in payload
    assert "processing_rate_per_minute" in payload
    assert "last_authorization_status" in payload
    assert "connection_live" in payload
    assert "charger_id" not in payload
    assert "recent_requests" not in payload
    assert ChargerTimelineProgress.objects.count() == before


def test_display_projection_only_queries_authoritative_snapshot(monkeypatch):
    expected = {
        "charger_identity": "isolated-display",
        "condition": "ready",
        "state": "ready",
        "pending_work": 0,
        "oldest_pending_age_seconds": None,
        "processing_rate_per_minute": 0.0,
        "max_processing_latency_seconds": None,
        "recent_request_errors": 0,
        "recent_outbound_errors": 0,
        "recent_retry_attempts": 0,
        "last_authorization_age_seconds": None,
        "last_authorization_status": None,
        "connection_live": True,
        "as_of": "2026-09-28T03:00:00+00:00",
    }
    calls = []

    def query(identity):
        calls.append(identity)
        return expected

    monkeypatch.setattr(display_status, "query_timeline_status", query)

    payload = display_status.query_display_status("isolated-display")

    assert calls == ["isolated-display"]
    assert tuple(payload) == DISPLAY_STATUS_FIELDS
    assert payload["condition"] == "ready"
    assert payload["pending_work"] == 0
    assert payload["connection_live"] is True

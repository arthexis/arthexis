import json
from datetime import datetime, timedelta, timezone

import pytest
from django.core.management import call_command

from apps.ocpp.models import ChargerTimelineProgress
from apps.ocpp.services.display_status import query_display_status
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db(transaction=True)


def test_display_projection_is_compact_and_read_only(capsys):
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


def test_display_status_command_emits_json():
    selected = charger("display-json")

    from io import StringIO

    stdout = StringIO()
    call_command("ocpp_status", charger=selected.identity, json_output=True, stdout=stdout)
    payload = json.loads(stdout.getvalue())

    assert payload["charger"] == selected.identity
    assert payload["condition"] == "unknown"
    assert payload["connection_live"] is False

from datetime import datetime, timezone

from apps.ocpp.services.timeline_evidence import newest_event_at


def test_meter_values_uses_newest_valid_timestamp():
    payload = {
        "meterValue": [
            {"timestamp": "2023-01-01T08:00:00Z"},
            {"timestamp": "not-a-time"},
            {"timestamp": "2023-02-01T08:00:00Z"},
        ]
    }

    assert newest_event_at("MeterValues", payload) == datetime(
        2023, 2, 1, 8, tzinfo=timezone.utc
    )


def test_transaction_event_uses_explicit_timestamp():
    assert newest_event_at(
        "TransactionEvent", {"timestamp": "2026-09-27T14:00:00Z"}
    ) == datetime(2026, 9, 27, 14, tzinfo=timezone.utc)


def test_missing_or_malformed_timestamp_is_not_timeline_evidence():
    assert newest_event_at("MeterValues", {"meterValue": [{}]}) is None
    assert newest_event_at("StartTransaction", {"timestamp": "broken"}) is None


def test_transport_only_actions_do_not_invent_timeline_evidence():
    assert newest_event_at("Heartbeat", {"timestamp": "2026-09-27T14:00:00Z"}) is None
    assert newest_event_at("BootNotification", {}) is None

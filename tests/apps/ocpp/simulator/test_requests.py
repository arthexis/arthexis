import json

from apps.ocpp.simulator.requests import RequestJournal


def test_request_journal_persists_correlation_without_raw_id_tag(tmp_path):
    journal = RequestJournal(tmp_path)
    request_id = journal.new_request_id()

    journal.submitted(
        request_id,
        charger="GW001",
        id_tag="SECRET-RFID-001",
        charger_time="2026-10-01T00:00:00Z",
    )
    journal.completed(
        request_id,
        charger="GW001",
        authorization="Accepted",
        charger_time="2026-10-01T00:00:01Z",
    )

    event_text = (tmp_path / "events.jsonl").read_text()
    result_text = (tmp_path / "results.jsonl").read_text()
    assert "SECRET-RFID-001" not in event_text
    assert "SECRET-RFID-001" not in result_text

    event = json.loads(event_text)
    result = json.loads(result_text)
    assert event["request_id"] == request_id
    assert event["id_tag_sha256"] == RequestJournal.id_tag_fingerprint(
        "SECRET-RFID-001"
    )
    assert result["request_id"] == request_id
    assert result["authorization"] == "Accepted"
    assert (tmp_path / "events.jsonl").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "results.jsonl").stat().st_mode & 0o777 == 0o600


def test_request_journal_recovers_completed_results(tmp_path):
    first = RequestJournal(tmp_path)
    first.completed(
        "req-1",
        charger="GW001",
        authorization="Blocked",
        charger_time="2026-10-01T00:00:01Z",
    )

    recovered = RequestJournal(tmp_path)

    assert recovered.result("req-1")["authorization"] == "Blocked"

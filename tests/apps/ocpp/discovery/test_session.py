import json
from datetime import datetime, timezone

import pytest

from apps.ocpp.discovery import DiscoverySession


def read_events(session: DiscoverySession) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (session.path / "events.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]


def test_create_session_builds_report_layout_and_initial_event(tmp_path) -> None:
    created_at = datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc)

    session = DiscoverySession.create(
        tmp_path,
        session_id="field-test-001",
        created_at=created_at,
    )

    assert session.path == tmp_path / "discovery" / "field-test-001"
    assert {
        child.name for child in session.path.iterdir()
    } == {
        "manifest.json",
        "events.jsonl",
        "summary.json",
        "network",
        "traffic",
        "ocpp",
        "notes",
    }

    manifest = json.loads(
        (session.path / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest == {
        "created_at": "2026-09-28T18:00:00Z",
        "format_version": 1,
        "session_id": "field-test-001",
        "status": "active",
    }

    events = read_events(session)
    assert len(events) == 1
    assert events[0]["session_id"] == "field-test-001"
    assert events[0]["sequence"] == 1
    assert events[0]["event_type"] == "session_started"
    assert events[0]["category"] == "observation"


def test_record_preserves_sequence_and_evidence_category(tmp_path) -> None:
    session = DiscoverySession.create(tmp_path, session_id="sequence-test")

    observation = session.record(
        "dns_query",
        metadata={"name": "csms.example.test"},
    )
    inference = session.record(
        "csms_candidate",
        category="inference",
        metadata={"destination": "192.0.2.10:9000"},
        artifact_refs=("network/trace.json",),
    )
    note = session.record(
        "operator_note",
        category="operator_note",
        metadata={"text": "charger retried after link reset"},
    )

    assert [observation["sequence"], inference["sequence"], note["sequence"]] == [
        2,
        3,
        4,
    ]
    assert [event["category"] for event in read_events(session)] == [
        "observation",
        "observation",
        "inference",
        "operator_note",
    ]
    assert inference["artifact_refs"] == ["network/trace.json"]


def test_open_reads_existing_session_and_continues_sequence(tmp_path) -> None:
    session = DiscoverySession.create(tmp_path, session_id="open-test")
    session.record("link_up")

    reopened = DiscoverySession.open(tmp_path, "open-test")
    event = reopened.record("dns_query")

    assert reopened.created_at == session.created_at
    assert event["sequence"] == 3
    assert [item["event_type"] for item in reopened.events()] == [
        "session_started",
        "link_up",
        "dns_query",
    ]


def test_open_repairs_only_a_truncated_final_append(tmp_path) -> None:
    session = DiscoverySession.create(tmp_path, session_id="interrupted")
    session.record("link_up")
    events_path = session.path / "events.jsonl"

    with events_path.open("ab") as stream:
        stream.write(b'{"session_id":"interrupted","sequence":3,"timestamp":"2026-')
        stream.flush()

    reopened = DiscoverySession.open(tmp_path, "interrupted")
    recovered = reopened.record("dns_query")

    assert recovered["sequence"] == 3
    assert [event["event_type"] for event in reopened.events()] == [
        "session_started",
        "link_up",
        "dns_query",
    ]
    assert events_path.read_bytes().endswith(b"\n")


def test_open_rejects_corruption_inside_committed_stream(tmp_path) -> None:
    session = DiscoverySession.create(tmp_path, session_id="corrupt-middle")
    events_path = session.path / "events.jsonl"
    original = events_path.read_text(encoding="utf-8")
    events_path.write_text(
        original + "{not-json}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="line 2 is corrupt"):
        DiscoverySession.open(tmp_path, "corrupt-middle")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda event: event.update(session_id="other-session"),
            "belongs to another session",
        ),
        (
            lambda event: event.update(sequence=9),
            "must have sequence 1",
        ),
        (
            lambda event: event.update(category="guess"),
            "invalid category",
        ),
    ],
)
def test_open_rejects_inconsistent_committed_events(
    tmp_path, mutate, message: str
) -> None:
    session = DiscoverySession.create(tmp_path, session_id="invalid-event")
    events_path = session.path / "events.jsonl"
    event = json.loads(events_path.read_text(encoding="utf-8"))
    mutate(event)
    events_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        DiscoverySession.open(tmp_path, "invalid-event")


def test_summary_is_regenerated_from_authoritative_event_stream(tmp_path) -> None:
    session = DiscoverySession.create(tmp_path, session_id="summary-test")
    session.record("dns_query")
    session.record("csms_candidate", category="inference")
    session.record("operator_note", category="operator_note")

    stale_summary = {
        "session_id": "summary-test",
        "event_count": 999,
    }
    (session.path / "summary.json").write_text(
        json.dumps(stale_summary),
        encoding="utf-8",
    )

    summary = session.write_summary()

    assert summary["event_count"] == 4
    assert summary["last_sequence"] == 4
    assert summary["event_counts"] == {
        "session_started": 1,
        "dns_query": 1,
        "csms_candidate": 1,
        "operator_note": 1,
    }
    assert summary["category_counts"] == {
        "observation": 2,
        "inference": 1,
        "operator_note": 1,
    }
    persisted = json.loads(
        (session.path / "summary.json").read_text(encoding="utf-8")
    )
    assert persisted == summary


@pytest.mark.parametrize("summary_contents", [None, "{broken-json"])
def test_summary_can_be_rebuilt_when_missing_or_corrupt(
    tmp_path, summary_contents: str | None
) -> None:
    session = DiscoverySession.create(tmp_path, session_id="summary-recovery")
    session.record("dns_query")
    summary_path = session.path / "summary.json"

    if summary_contents is None:
        summary_path.unlink()
    else:
        summary_path.write_text(summary_contents, encoding="utf-8")

    reopened = DiscoverySession.open(tmp_path, "summary-recovery")
    rebuilt = reopened.write_summary()

    assert rebuilt["event_count"] == 2
    assert json.loads(summary_path.read_text(encoding="utf-8")) == rebuilt


def test_list_returns_only_valid_session_manifests_in_creation_order(tmp_path) -> None:
    DiscoverySession.create(
        tmp_path,
        session_id="later",
        created_at=datetime(2026, 9, 28, 19, 0, tzinfo=timezone.utc),
    )
    DiscoverySession.create(
        tmp_path,
        session_id="earlier",
        created_at=datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc),
    )
    ignored = tmp_path / "discovery" / "not-a-session"
    ignored.mkdir()
    (ignored / "junk.txt").write_text("ignored", encoding="utf-8")

    assert DiscoverySession.list(tmp_path) == [
        {
            "session_id": "earlier",
            "created_at": "2026-09-28T18:00:00Z",
            "status": "active",
        },
        {
            "session_id": "later",
            "created_at": "2026-09-28T19:00:00Z",
            "status": "active",
        },
    ]


@pytest.mark.parametrize(
    "session_id",
    ("../escape", "with/slash", "", "contains space"),
)
def test_create_rejects_unsafe_session_ids(tmp_path, session_id: str) -> None:
    with pytest.raises(ValueError):
        DiscoverySession.create(tmp_path, session_id=session_id)



def test_summary_derives_capture_status_from_event_stream(tmp_path) -> None:
    session = DiscoverySession.create(tmp_path, session_id="capture-summary")
    session.record("capture_requested", metadata={"candidate_available": True})
    session.record(
        "capture_unavailable",
        metadata={"reason": "capture_provider_unavailable"},
    )

    summary = session.write_summary()

    assert summary["capture"] == {
        "requested": True,
        "available": False,
        "attempted": False,
        "succeeded": False,
        "strategy": None,
        "failure_reason": "capture_provider_unavailable",
    }

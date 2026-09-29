import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
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
        "redirect_id": None,
        "active": False,
        "release_failure_reason": None,
    }



def test_summary_tracks_active_redirect_until_release(tmp_path) -> None:
    session = DiscoverySession.create(tmp_path, session_id="capture-active")
    session.record("capture_requested", metadata={"candidate_available": True})
    session.record(
        "capture_available",
        metadata={"provider": "gway", "strategy": "destination-redirect"},
    )
    session.record(
        "capture_started",
        metadata={
            "provider": "gway",
            "strategy": "destination-redirect",
            "redirect_id": "abc123def456",
        },
    )

    active = session.write_summary()["capture"]
    assert active["active"] is True
    assert active["redirect_id"] == "abc123def456"

    session.record(
        "capture_released",
        metadata={"redirect_id": "abc123def456", "changed": True},
    )

    released = session.write_summary()["capture"]
    assert released["active"] is False
    assert released["redirect_id"] == "abc123def456"



def test_concurrent_reopened_sessions_allocate_unique_sequences(tmp_path) -> None:
    DiscoverySession.create(tmp_path, session_id="concurrent")

    def append(index: int) -> int:
        session = DiscoverySession.open(tmp_path, "concurrent")
        return session.record(
            "traffic_observed",
            metadata={"index": index},
        )["sequence"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        sequences = sorted(executor.map(append, range(20)))

    assert sequences == list(range(2, 22))
    persisted = DiscoverySession.open(tmp_path, "concurrent").events()
    assert [event["sequence"] for event in persisted] == list(range(1, 22))


@pytest.mark.parametrize(
    "artifact_path",
    ("../outside.bin", "/absolute.bin", "nested/../outside.bin"),
)
def test_artifact_paths_cannot_escape_session(tmp_path, artifact_path) -> None:
    session = DiscoverySession.create(tmp_path, session_id="artifact-safe")

    with pytest.raises(ValueError, match="artifact path"):
        session.write_artifact(artifact_path, b"evidence")


def test_write_artifact_returns_relative_path_hash_and_size(tmp_path) -> None:
    session = DiscoverySession.create(tmp_path, session_id="artifact")
    content = b"pcap-or-debug-evidence"

    artifact = session.write_artifact("traffic/sample.bin", content)

    assert artifact == {
        "path": "traffic/sample.bin",
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }
    assert (session.path / "traffic" / "sample.bin").read_bytes() == content


def test_handoff_arm_and_claim_are_durable_and_single_use(tmp_path) -> None:
    session = DiscoverySession.create(tmp_path, session_id="handoff")
    session.arm_handoff(
        charger_identity="CP001",
        client_host="192.0.2.40",
        original_destination={"host": "vendor.example", "port": 9000},
        strategy="destination-redirect",
    )

    assert session.handoff_path.exists()
    assert session.write_summary()["handoff_armed"] is True
    assert (
        session.claim_handoff(
            charger_identity="OTHER",
            client_host="192.0.2.40",
        )
        is None
    )

    claimed = DiscoverySession.open(tmp_path, "handoff").claim_handoff(
        charger_identity="CP001",
        client_host="192.0.2.40",
    )

    assert claimed is not None
    reopened = DiscoverySession.open(tmp_path, "handoff")
    assert not reopened.handoff_path.exists()
    assert reopened.claimed_handoff_path.exists()
    assert reopened.write_summary()["handoff_claimed"] is True
    assert (
        reopened.claim_handoff(
            charger_identity="CP001",
            client_host="192.0.2.40",
        )
        is None
    )


def test_handoff_can_match_source_host_without_known_identity(tmp_path) -> None:
    session = DiscoverySession.create(tmp_path, session_id="handoff-host")
    session.arm_handoff(
        client_host="198.51.100.8",
        strategy="destination-redirect",
    )

    claimed = session.claim_handoff(
        charger_identity="learned-from-ocpp-path",
        client_host="198.51.100.8",
    )

    assert claimed is not None


def test_handoff_requires_match_key(tmp_path) -> None:
    session = DiscoverySession.create(tmp_path, session_id="handoff-invalid")

    with pytest.raises(ValueError, match="charger_identity and/or client_host"):
        session.arm_handoff()

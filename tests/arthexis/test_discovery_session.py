import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from arthexis.discovery import DiscoveryStore, EvidenceKind


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(seconds=1)
        return current


def test_create_session_writes_manifest_event_and_summary(tmp_path) -> None:
    store = DiscoveryStore(tmp_path / "discovery", now=Clock())

    session = store.create(interface="eth0", session_id="field-001")

    manifest = json.loads(session.manifest_path.read_text())
    summary = json.loads(session.summary_path.read_text())
    events = list(session.events())

    assert manifest == {
        "schema_version": 1,
        "session_id": "field-001",
        "created_at": "2026-09-26T15:00:00Z",
        "interface": "eth0",
    }
    assert events[0]["seq"] == 1
    assert events[0]["type"] == "session_started"
    assert events[0]["data"] == {"interface": "eth0"}
    assert summary["status"] == "active"
    assert summary["event_count"] == 1


def test_events_separate_observation_note_and_inference(tmp_path) -> None:
    session = DiscoveryStore(
        tmp_path / "discovery", now=Clock()
    ).create(session_id="field-002")

    observation = session.append("dns_query", data={"host": "vendor.example"})
    note = session.append(
        "operator_note",
        kind=EvidenceKind.NOTE,
        data={"text": "charger cabinet is locked"},
    )
    inference = session.append(
        "csms_candidate",
        kind=EvidenceKind.INFERENCE,
        data={"host": "vendor.example", "confidence": "high"},
    )

    assert observation["kind"] == "observation"
    assert note["kind"] == "note"
    assert inference["kind"] == "inference"
    assert [event["seq"] for event in session.events()] == [1, 2, 3, 4]


def test_reopening_interrupted_session_continues_sequence(tmp_path) -> None:
    store = DiscoveryStore(tmp_path / "discovery", now=Clock())
    session = store.create(session_id="field-003")
    session.append("link_up")

    reopened = store.open("field-003")
    event = reopened.append("traffic_observed")

    assert event["seq"] == 3
    assert reopened.summarize()["status"] == "active"
    assert reopened.summarize()["last_seq"] == 3


def test_reader_ignores_incomplete_trailing_event_after_interruption(tmp_path) -> None:
    store = DiscoveryStore(tmp_path / "discovery", now=Clock())
    session = store.create(session_id="field-004")
    session.events_path.write_text(
        session.events_path.read_text() + '{"seq":999',
        encoding="utf-8",
    )

    assert [event["seq"] for event in session.events()] == [1]


def test_append_repairs_incomplete_trailing_event_after_interruption(tmp_path) -> None:
    store = DiscoveryStore(tmp_path / "discovery", now=Clock())
    session = store.create(session_id="field-004-recovered")
    with session.events_path.open("a", encoding="utf-8") as stream:
        stream.write('{"seq":999')

    event = store.open("field-004-recovered").append("traffic_observed")

    assert event["seq"] == 2
    assert [item["seq"] for item in session.events()] == [1, 2]


def test_complete_derives_capture_and_charger_summary(tmp_path) -> None:
    session = DiscoveryStore(
        tmp_path / "discovery", now=Clock()
    ).create(session_id="field-005")
    session.append(
        "csms_candidate",
        kind=EvidenceKind.INFERENCE,
        data={"host": "ocpp.vendor.example", "port": 443},
    )
    session.append("capture_started", data={"strategy": "dns_redirect"})
    session.append(
        "ocpp_connection",
        data={"charger": {"id": 47, "identity": "CP001"}},
    )
    session.append("capture_succeeded")

    summary = session.complete()

    assert summary["status"] == "completed"
    assert summary["csms_candidate_count"] == 1
    assert summary["capture_attempted"] is True
    assert summary["capture_succeeded"] is True
    assert summary["charger"] == {"id": 47, "identity": "CP001"}
    assert json.loads(session.summary_path.read_text()) == summary


def test_store_lists_and_reads_sessions_by_id(tmp_path) -> None:
    store = DiscoveryStore(tmp_path / "discovery", now=Clock())
    store.create(session_id="b-session")
    store.create(session_id="a-session")

    assert store.list() == ["a-session", "b-session"]
    assert store.open("a-session").event(1)["type"] == "session_started"

    with pytest.raises(KeyError, match="Unknown discovery session"):
        store.open("missing")


def test_concurrent_appends_get_unique_monotonic_sequences(tmp_path) -> None:
    store = DiscoveryStore(tmp_path / "discovery", now=Clock())
    store.create(session_id="field-concurrent")

    def append(index: int) -> int:
        session = store.open("field-concurrent")
        return session.append("traffic_observed", data={"index": index})["seq"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        sequences = sorted(executor.map(append, range(20)))

    assert sequences == list(range(2, 22))
    persisted = list(store.open("field-concurrent").events())
    assert [event["seq"] for event in persisted] == list(range(1, 22))


@pytest.mark.parametrize(
    "session_id",
    ["../escape", "/absolute", "nested/path", ".hidden/child"],
)
def test_session_ids_cannot_escape_discovery_root(tmp_path, session_id) -> None:
    store = DiscoveryStore(tmp_path / "discovery", now=Clock())

    with pytest.raises(ValueError, match="Discovery session ID"):
        store.create(session_id=session_id)


@pytest.mark.parametrize(
    "artifact_path",
    ["../outside.bin", "/absolute.bin", "nested/../outside.bin"],
)
def test_artifact_paths_cannot_escape_session(tmp_path, artifact_path) -> None:
    session = DiscoveryStore(
        tmp_path / "discovery", now=Clock()
    ).create(session_id="field-artifact-safe")

    with pytest.raises(ValueError, match="artifact path"):
        session.write_artifact(artifact_path, b"evidence")


def test_write_artifact_returns_relative_path_hash_and_size(tmp_path) -> None:
    session = DiscoveryStore(
        tmp_path / "discovery", now=Clock()
    ).create(session_id="field-artifact")
    content = b"pcap-or-debug-evidence"

    artifact = session.write_artifact("traffic/sample.bin", content)
    event = session.append(
        "traffic_artifact",
        data={"sha256": artifact["sha256"], "bytes": artifact["bytes"]},
        artifact=artifact["path"],
    )

    assert artifact == {
        "path": "traffic/sample.bin",
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }
    assert (session.path / "traffic" / "sample.bin").read_bytes() == content
    assert event["artifact"] == "traffic/sample.bin"


def test_append_rejects_non_json_evidence_before_writing(tmp_path) -> None:
    session = DiscoveryStore(
        tmp_path / "discovery", now=Clock()
    ).create(session_id="field-006")

    with pytest.raises(TypeError):
        session.append("bad", data={"value": object()})

    assert [event["seq"] for event in session.events()] == [1]

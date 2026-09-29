import json

import pytest
from django.test import override_settings

from apps.events.models import EventEnvelope
from apps.ocpp.discovery.projection import project_discovery_event
from apps.ocpp.discovery.session import DiscoverySession
from apps.ocpp.services.discovery_handoff import (
    arm_discovery_handoff,
    claim_and_record_discovery_handoff,
)
from tests.apps.ocpp.builders import charger


pytestmark = pytest.mark.django_db


def create_session(tmp_path, session_id, *, interface=None):
    session = DiscoverySession.create(
        tmp_path,
        session_id=session_id,
        projector=project_discovery_event,
    )
    if interface is not None:
        session.record(
            "interface_selected",
            metadata={"interface": interface, "role": "control"},
        )
    return session


def test_normal_ocpp_connection_finalizes_matching_discovery_handoff(tmp_path) -> None:
    with override_settings(DATA_DIR=tmp_path):
        session = create_session(tmp_path, "capture-001", interface="eth0")
        arm_discovery_handoff(
            "capture-001",
            charger_identity="CP001",
            client_host="192.0.2.20",
            original_destination={"host": "old.csms.example", "port": 9000},
            strategy="destination-redirect",
        )
        selected = charger("CP001")

        claimed = claim_and_record_discovery_handoff(
            charger=selected,
            scope={
                "client": ("192.0.2.20", 43123),
                "path": "/ocpp/CP001",
            },
            protocol="ocpp1.6",
            offered_subprotocols=[],
        )

        assert claimed is not None
        assert claimed.session_id == "capture-001"
        events = claimed.events()
        assert [event["event_type"] for event in events[-3:]] == [
            "redirect_observed",
            "ocpp_connection",
            "capture_succeeded",
        ]
        connection = events[-2]["metadata"]
        assert connection["charger"] == {
            "id": selected.pk,
            "identity": "CP001",
        }
        assert connection["protocol"] == "ocpp1.6"
        assert connection["offered_subprotocols"] == []
        summary = json.loads((claimed.path / "summary.json").read_text())
        assert summary["capture"]["succeeded"] is True
        assert summary["handoff_claimed"] is True
        assert summary["charger"] == {
            "id": selected.pk,
            "identity": "CP001",
        }


def test_unrelated_ocpp_connection_does_not_claim_capture(tmp_path) -> None:
    with override_settings(DATA_DIR=tmp_path):
        session = create_session(tmp_path, "capture-other")
        session.arm_handoff(charger_identity="EXPECTED")
        selected = charger("UNRELATED")

        claimed = claim_and_record_discovery_handoff(
            charger=selected,
            scope={"client": ("203.0.113.10", 12345), "path": "/ocpp/UNRELATED"},
            protocol="ocpp1.6",
            offered_subprotocols=["ocpp1.6"],
        )

        assert claimed is None
        assert session.handoff_path.exists()
        assert not any(
            event["event_type"] == "ocpp_connection"
            for event in session.events()
        )


def test_discovery_events_project_compact_durable_references(tmp_path) -> None:
    with override_settings(DATA_DIR=tmp_path):
        session = create_session(tmp_path, "project-001", interface="eth0")
        persisted = session.record(
            "traffic_observed",
            metadata={"large_or_private_detail": "stays in discovery evidence"},
            artifact_refs=("traffic/sample.pcap",),
        )

        envelope = EventEnvelope.objects.filter(
            event_type="discovery.event",
            payload__session_id="project-001",
            payload__sequence=persisted["sequence"],
        ).get()

        assert envelope.producer == "arthexis.ocpp.discovery"
        assert envelope.payload == {
            "session_id": "project-001",
            "sequence": persisted["sequence"],
            "event_type": "traffic_observed",
            "category": "observation",
            "artifact_refs": ["traffic/sample.pcap"],
        }
        stored = session.events()[persisted["sequence"] - 1]
        assert stored["metadata"] == {
            "large_or_private_detail": "stays in discovery evidence"
        }


def test_event_projection_failure_does_not_lose_discovery_evidence(
    tmp_path, monkeypatch
) -> None:
    with override_settings(DATA_DIR=tmp_path):
        def unavailable(*args, **kwargs):
            raise RuntimeError("event store unavailable")

        session = DiscoverySession.create(
            tmp_path,
            session_id="project-outage",
            projector=unavailable,
        )
        persisted = session.record("dns_query", metadata={"name": "csms.example"})

        reopened = DiscoverySession.open(tmp_path, "project-outage")
        stored = reopened.events()[persisted["sequence"] - 1]
        assert stored == persisted
        assert not EventEnvelope.objects.filter(
            payload__session_id="project-outage",
            payload__sequence=persisted["sequence"],
        ).exists()

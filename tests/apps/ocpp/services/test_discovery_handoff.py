import json

import pytest
from django.test import override_settings

from apps.events.models import EventEnvelope
from apps.ocpp.services.discovery_handoff import (
    arm_discovery_handoff,
    claim_and_record_discovery_handoff,
    discovery_store,
)
from tests.apps.ocpp.builders import charger


pytestmark = pytest.mark.django_db


def test_normal_ocpp_connection_finalizes_matching_discovery_handoff(tmp_path) -> None:
    with override_settings(DATA_DIR=tmp_path):
        session = discovery_store().create(interface="eth0", session_id="capture-001")
        arm_discovery_handoff(
            "capture-001",
            charger_identity="CP001",
            client_host="192.0.2.20",
            original_destination={"host": "old.csms.example", "port": 9000},
            strategy="dnat",
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
        events = list(claimed.events())
        assert [event["type"] for event in events[-3:]] == [
            "redirect_observed",
            "ocpp_connection",
            "capture_succeeded",
        ]
        connection = events[-2]["data"]
        assert connection["charger"] == {
            "id": selected.pk,
            "identity": "CP001",
        }
        assert connection["protocol"] == "ocpp1.6"
        assert connection["offered_subprotocols"] == []
        summary = json.loads(claimed.summary_path.read_text())
        assert summary["capture_succeeded"] is True
        assert summary["handoff_claimed"] is True
        assert summary["charger"] == {
            "id": selected.pk,
            "identity": "CP001",
        }


def test_unrelated_ocpp_connection_does_not_claim_capture(tmp_path) -> None:
    with override_settings(DATA_DIR=tmp_path):
        session = discovery_store().create(session_id="capture-other")
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
            event["type"] == "ocpp_connection"
            for event in session.events()
        )



def test_discovery_events_project_compact_durable_references(tmp_path) -> None:
    with override_settings(DATA_DIR=tmp_path):
        session = discovery_store().create(interface="eth0", session_id="project-001")
        persisted = session.append(
            "traffic_observed",
            data={"large_or_private_detail": "stays in discovery evidence"},
            artifact="traffic/sample.pcap",
        )

        envelope = EventEnvelope.objects.filter(
            event_type="discovery.event",
            payload__session_id="project-001",
            payload__sequence=persisted["seq"],
        ).get()

        assert envelope.producer == "arthexis.discovery"
        assert envelope.payload == {
            "session_id": "project-001",
            "sequence": persisted["seq"],
            "event_type": "traffic_observed",
            "kind": "observation",
            "artifact": "traffic/sample.pcap",
        }
        assert session.event(persisted["seq"])["data"] == {
            "large_or_private_detail": "stays in discovery evidence"
        }


def test_event_projection_failure_does_not_lose_discovery_evidence(
    tmp_path, monkeypatch
) -> None:
    with override_settings(DATA_DIR=tmp_path):
        session = discovery_store().create(session_id="project-outage")

        def unavailable(*args, **kwargs):
            raise RuntimeError("event store unavailable")

        monkeypatch.setattr(
            "apps.ocpp.services.discovery_handoff.publish_safely",
            unavailable,
        )
        persisted = session.append("dns_query", data={"name": "csms.example"})

        reopened = discovery_store().open("project-outage")
        assert reopened.event(persisted["seq"]) == persisted
        assert not EventEnvelope.objects.filter(
            payload__session_id="project-outage",
            payload__sequence=persisted["seq"],
        ).exists()

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from apps.cards.agent_card import (
    _expected_reader_proof,
    AgentCardError,
    build_agent_card_sector_payloads,
    parse_agent_card,
    plan_agent_activation,
    score_soul_identity,
)


def valid_agent_card_records() -> list[str]:
    return [
        "AC1|M|S=4|X=10|ALG=B2S8|POL=RDRSIG",
        "AC1|I1|NS=SOUL|ID=7G4P2K|H=3MF4DA8C2E1B",
        "AC1|I2|NS=SOUL|ID=7G4P2K|H=3MF4DA8C2E2C",
        "AC1|I3|VOID=1",
        "AC1|I4|VOID=1",
        "AC1|K01|SIG=[AGENT.SKILL:TRIAGE]|H=A91B22",
        "AC1|F02|T=NOTE|H=A91B22|TXT=BRIEF-HINT",
        "AC1|K03|SIG=[AGENT.SKILL:RFID]|H=A91B23",
        "AC1|F04|T=NOTE|H=A91B23|TXT=RFID",
        "AC1|K05|SIG=[AGENT.SKILL:EMAIL]|H=A91B24",
        "AC1|F06|T=NOTE|H=A91B24|TXT=EMAIL",
        "AC1|K07|SIG=[AGENT.SKILL:QUOTE]|H=A91B25",
        "AC1|F08|T=NOTE|H=A91B25|TXT=QUOTE",
        "AC1|K09|SIG=[AGENT.SKILL:SCAN]|H=A91B26",
        "AC1|F10|T=NOTE|H=A91B26|TXT=SCAN",
    ]


def test_parse_agent_card_accepts_valid_v1_records():
    card = parse_agent_card(valid_agent_card_records())

    assert card.manifest.slot_code == "M"
    assert len(card.identity_slots) == 4
    assert card.capability_sigils() == [
        "[AGENT.SKILL:TRIAGE]",
        "[AGENT.SKILL:RFID]",
        "[AGENT.SKILL:EMAIL]",
        "[AGENT.SKILL:QUOTE]",
        "[AGENT.SKILL:SCAN]",
    ]
    assert len(card.fingerprint) == 64


def test_parse_agent_card_rejects_blank_identity_sector():
    records = valid_agent_card_records()
    records[2] = " " * 48

    with pytest.raises(AgentCardError, match="blank"):
        parse_agent_card(records)


def test_parse_agent_card_rejects_wrong_slot_sector():
    records = valid_agent_card_records()
    records[1] = "AC1|I2|VOID=1"

    with pytest.raises(AgentCardError, match="wrong slot code"):
        parse_agent_card(records)


def test_parse_agent_card_rejects_script_like_payloads():
    records = valid_agent_card_records()
    records[6] = "AC1|F02|T=NOTE|TXT=python"

    with pytest.raises(AgentCardError, match="script-like"):
        parse_agent_card(records)


def test_parse_agent_card_rejects_non_ascii_string_payloads():
    records = valid_agent_card_records()
    records[6] = "AC1|F02|T=NOTE|TXT=cafe\u0301"

    with pytest.raises(AgentCardError, match="non-ASCII"):
        parse_agent_card(records)


@pytest.mark.parametrize("sector_payloads", [None, 7, "AC1|M|S=4", b"AC1|M|S=4"])
def test_parse_agent_card_rejects_non_iterable_or_scalar_payloads(sector_payloads):
    with pytest.raises(AgentCardError, match="mapping or iterable"):
        parse_agent_card(sector_payloads)


def test_build_agent_card_sector_payloads_returns_valid_complete_payload():
    build = build_agent_card_sector_payloads(
        identity_sources={
            "intent": "intent:1",
            "bundle": "bundle:2",
            "interface": "interface:3",
            "card": "AABBCCDD",
        },
        skill_slugs=["rfid-triage"],
    )

    assert sorted(build.sector_records) == list(range(1, 16))
    assert all(len(record.encode("ascii")) <= 48 for record in build.sector_records.values())
    assert all(len(record.encode("ascii")) == 48 for record in build.padded_sector_records.values())
    parsed = parse_agent_card(build.sector_records)
    assert parsed.fingerprint == build.fingerprint
    assert parsed.capability_sigils() == ["[AGENT.SKILL:rfid-triage]"]


def test_build_agent_card_sector_payloads_omits_oversized_skill_sigils():
    build = build_agent_card_sector_payloads(
        identity_sources={"intent": "intent", "bundle": "bundle", "interface": "interface", "card": "AABB"},
        skill_slugs=["skill-" + "x" * 80, "rfid-triage"],
    )

    assert build.omitted_skill_sigils
    assert build.compatibility_notes
    assert parse_agent_card(build.sector_records).capability_sigils() == [
        "[AGENT.SKILL:rfid-triage]"
    ]


def test_build_agent_card_sector_payloads_reports_overflow_once():
    build = build_agent_card_sector_payloads(
        identity_sources={"intent": "intent", "bundle": "bundle", "interface": "interface", "card": "AABB"},
        skill_slugs=[f"s{index}" for index in range(12)],
    )

    assert len(build.compatibility_notes) == 1
    assert build.omitted_skill_sigils == ["[AGENT.SKILL:s10]", "[AGENT.SKILL:s11]"]


def test_score_soul_identity_returns_best_candidate():
    card = parse_agent_card(valid_agent_card_records())

    score = score_soul_identity(
        card,
        [
            {"id": "weak", "seeds": [{"NS": "SOUL", "ID": "NOPE", "H": "NOPE"}]},
            {
                "id": "best",
                "seeds": [
                    {"NS": "SOUL", "ID": "7G4P2K", "H": "3MF4DA8C2E1B"},
                    {"NS": "SOUL", "ID": "7G4P2K", "H": "3MF4DA8C2E2C"},
                ],
            },
        ],
    )

    assert score.candidate_id == "best"
    assert score.matching_seeds == 2
    assert score.confidence == "plausible"


def test_plan_agent_activation_rejects_unknown_reader_trust():
    card = parse_agent_card(valid_agent_card_records())

    plan = plan_agent_activation(
        card,
        {"reader_id": "reader-1", "node_id": "node-1", "trust_tier": "unknown"},
        skill_bundle_id=1,
        interface_spec_id=2,
    )

    assert plan.accepted is False
    assert plan.status == "rejected"
    assert "trusted" in plan.reason


def test_plan_agent_activation_accepts_trusted_reader_with_bundle_and_interface():
    card = parse_agent_card(valid_agent_card_records())
    observed_at = datetime.now(timezone.utc).isoformat()

    plan = plan_agent_activation(
        card,
        {
            "reader_id": "reader-1",
            "node_id": "node-1",
            "trust_tier": "trusted_operator_console",
            "observed_at": observed_at,
            "proof": _expected_reader_proof(
                trust_tier="trusted_operator_console",
                reader_id="reader-1",
                node_id="node-1",
                observed_at=observed_at,
                manifest_fingerprint=card.fingerprint,
            ),
        },
        skill_bundle_id=1,
        interface_spec_id=2,
    )

    assert plan.accepted is True
    assert plan.status == "ready"
    assert plan.capability_sigils


def test_plan_agent_activation_accepts_datetime_reader_timestamp():
    card = parse_agent_card(valid_agent_card_records())
    observed_at = datetime.now(timezone.utc)

    plan = plan_agent_activation(
        card,
        {
            "reader_id": "reader-1",
            "node_id": "node-1",
            "trust_tier": "trusted_operator_console",
            "observed_at": observed_at,
            "proof": _expected_reader_proof(
                trust_tier="trusted_operator_console",
                reader_id="reader-1",
                node_id="node-1",
                observed_at=observed_at,
                manifest_fingerprint=card.fingerprint,
            ),
        },
        skill_bundle_id=1,
        interface_spec_id=2,
    )

    assert plan.accepted is True
    assert plan.status == "ready"


def test_plan_agent_activation_rejects_future_reader_timestamp():
    card = parse_agent_card(valid_agent_card_records())
    observed_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()

    plan = plan_agent_activation(
        card,
        {
            "reader_id": "reader-1",
            "node_id": "node-1",
            "trust_tier": "trusted_operator_console",
            "observed_at": observed_at,
            "proof": _expected_reader_proof(
                trust_tier="trusted_operator_console",
                reader_id="reader-1",
                node_id="node-1",
                observed_at=observed_at,
                manifest_fingerprint=card.fingerprint,
            ),
        },
        skill_bundle_id=1,
        interface_spec_id=2,
    )

    assert plan.accepted is False
    assert plan.status == "rejected"
    assert "future" in plan.reason


def test_plan_agent_activation_rejects_unbound_reader_proof():
    card = parse_agent_card(valid_agent_card_records())

    plan = plan_agent_activation(
        card,
        {
            "reader_id": "reader-1",
            "node_id": "node-1",
            "trust_tier": "trusted_operator_console",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "proof": "not-a-signature",
        },
        skill_bundle_id=1,
        interface_spec_id=2,
    )

    assert plan.accepted is False
    assert plan.status == "rejected"
    assert "invalid" in plan.reason


def test_plan_agent_activation_rejects_plain_hash_reader_proof():
    card = parse_agent_card(valid_agent_card_records())
    observed_at = datetime.now(timezone.utc).isoformat()
    payload = "|".join(
        (
            "trusted_operator_console",
            "reader-1",
            "node-1",
            observed_at,
            card.fingerprint,
        )
    )

    plan = plan_agent_activation(
        card,
        {
            "reader_id": "reader-1",
            "node_id": "node-1",
            "trust_tier": "trusted_operator_console",
            "observed_at": observed_at,
            "proof": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        },
        skill_bundle_id=1,
        interface_spec_id=2,
    )

    assert plan.accepted is False
    assert plan.status == "rejected"
    assert "invalid" in plan.reason

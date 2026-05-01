from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.cards.agent_card import (
    AgentCardError,
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

    plan = plan_agent_activation(
        card,
        {
            "reader_id": "reader-1",
            "node_id": "node-1",
            "trust_tier": "trusted_operator_console",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "proof": "signed",
        },
        skill_bundle_id=1,
        interface_spec_id=2,
    )

    assert plan.accepted is True
    assert plan.status == "ready"
    assert plan.capability_sigils

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.cards.agent_card import parse_agent_card
from apps.cards.models import RFID
from apps.skills.models import AgentSkill, AgentSkillFile
from apps.souls.models import (
    AgentInterfaceSpec,
    CardSession,
    SkillBundle,
    SoulIntent,
    SoulSeedCard,
)
from apps.souls.services import (
    compose_skill_bundle,
    evict_card_session,
    provision_soul_seed_card,
    search_agent_skills,
)


def _valid_agent_card_records() -> list[str]:
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


@pytest.fixture
def skill(db):
    skill = AgentSkill.objects.create(
        slug="rfid-triage",
        title="RFID Triage",
        markdown="Diagnose RFID reader problems, scanner service health, and card events.",
    )
    AgentSkillFile.objects.create(
        skill=skill,
        relative_path="references/rfid.md",
        content="Use scan attempts, reader trust, and card UID evidence.",
        content_sha256="a" * 64,
        included_by_default=True,
    )
    return skill


@pytest.mark.django_db
def test_search_agent_skills_scores_registered_skill_package_content(skill):
    matches = search_agent_skills("rfid reader problem")

    assert matches
    assert matches[0].slug == skill.slug
    assert matches[0].score > 0
    assert matches[0].reasons


@pytest.mark.django_db
def test_search_agent_skills_rejects_negative_limit(skill):
    with pytest.raises(ValueError, match="non-negative"):
        search_agent_skills("rfid reader problem", limit=-1)


@pytest.mark.django_db
def test_compose_skill_bundle_dry_run_returns_bundle_plan(skill):
    summary = compose_skill_bundle("rfid-triage", dry_run=True)

    assert summary["dry_run"] is True
    assert summary["bundle"]["match_strategy"] == SkillBundle.MatchStrategy.EXACT
    assert summary["bundle"]["primary_skill"] == skill.slug
    assert summary["interface_spec"]["schema"]["schema_version"] == "soul_seed.interface.v1"


@pytest.mark.django_db
def test_compose_skill_bundle_can_persist_foundation_records(skill):
    summary = compose_skill_bundle("rfid reader problem", dry_run=False)

    assert summary["dry_run"] is False
    assert SoulIntent.objects.filter(pk=summary["intent"]["id"]).exists()
    assert SkillBundle.objects.filter(pk=summary["bundle"]["id"], skills=skill).exists()
    assert AgentInterfaceSpec.objects.filter(pk=summary["interface_spec"]["id"]).exists()


@pytest.mark.django_db
def test_evict_card_session_clears_runtime_fields():
    session = CardSession.objects.create(
        state=CardSession.State.ACTIVE,
        activation_plan={"status": "ready"},
        runtime_namespace="soul-seed-123",
    )

    evict_card_session(session, reason="card switch")
    session.refresh_from_db()

    assert session.state == CardSession.State.EVICTED
    assert session.activation_plan == {}
    assert session.runtime_namespace == ""
    assert session.eviction_reason == "card switch"
    assert session.ended_at is not None


@pytest.mark.django_db
def test_soul_seed_compose_command_outputs_json(skill):
    stdout = StringIO()

    call_command("soul_seed", "compose", "--prompt", "rfid reader problem", stdout=stdout)
    summary = json.loads(stdout.getvalue())

    assert summary["dry_run"] is True
    assert summary["matches"][0]["slug"] == skill.slug


@pytest.mark.django_db
def test_soul_seed_compose_command_rejects_negative_limit(skill):
    with pytest.raises(CommandError, match="non-negative"):
        call_command("soul_seed", "compose", "--prompt", "rfid reader problem", "--limit", "-1")


@pytest.mark.django_db
def test_provision_soul_seed_card_dry_run_returns_valid_agent_card(skill):
    summary = provision_soul_seed_card(
        "rfid reader problem",
        card_uid="aa bb cc dd",
        dry_run=True,
    )

    assert summary["dry_run"] is True
    assert summary["card"]["card_uid"] == "AABBCCDD"
    assert SoulSeedCard.objects.count() == 0
    card = parse_agent_card(summary["sector_records"])
    assert card.fingerprint == summary["card"]["manifest_fingerprint"]
    assert card.capability_sigils() == ["[AGENT.SKILL:rfid-triage]"]


@pytest.mark.django_db
def test_provision_soul_seed_card_write_persists_registry_records(skill):
    summary = provision_soul_seed_card(
        "rfid reader problem",
        card_uid="AABBCCDD",
        dry_run=False,
    )

    soul_seed_card = SoulSeedCard.objects.get(pk=summary["card"]["id"])
    assert summary["dry_run"] is False
    assert summary["card"]["created"] is True
    assert soul_seed_card.card_uid == "AABBCCDD"
    assert soul_seed_card.rfid == RFID.objects.get(rfid="AABBCCDD")
    assert soul_seed_card.intent_id == summary["intent"]["id"]
    assert soul_seed_card.skill_bundle_id == summary["bundle"]["id"]
    assert soul_seed_card.interface_spec_id == summary["interface_spec"]["id"]
    assert soul_seed_card.manifest_fingerprint == parse_agent_card(
        summary["sector_records"]
    ).fingerprint


@pytest.mark.django_db
def test_provision_soul_seed_card_write_updates_active_card_for_duplicate_uid(skill):
    first = provision_soul_seed_card("rfid reader problem", card_uid="AABBCCDD", dry_run=False)
    second = provision_soul_seed_card("rfid-triage", card_uid="AABBCCDD", dry_run=False)

    assert second["card"]["created"] is False
    assert second["card"]["id"] == first["card"]["id"]
    assert SoulSeedCard.objects.filter(card_uid="AABBCCDD").count() == 1


@pytest.mark.django_db
def test_provision_soul_seed_card_records_oversized_skill_note():
    long_skill = AgentSkill.objects.create(
        slug="skill-" + "x" * 80,
        title="Oversized Skill",
        markdown="oversized card payload test",
    )

    summary = provision_soul_seed_card(long_skill.slug, card_uid="AABBCCDD", dry_run=False)
    bundle = SkillBundle.objects.get(pk=summary["bundle"]["id"])

    assert summary["omitted_skill_sigils"]
    assert summary["compatibility_notes"]
    assert bundle.compatibility_notes == summary["compatibility_notes"]
    assert parse_agent_card(summary["sector_records"]).capability_sigils() == []


@pytest.mark.django_db
def test_soul_seed_provision_command_outputs_json_and_sector_file(skill, tmp_path):
    stdout = StringIO()
    sectors_path = tmp_path / "sectors.json"

    call_command(
        "soul_seed",
        "provision",
        "--prompt",
        "rfid reader problem",
        "--card-uid",
        "AABBCCDD",
        "--json",
        "--sectors-json-out",
        str(sectors_path),
        stdout=stdout,
    )
    summary = json.loads(stdout.getvalue())
    sector_records = json.loads(sectors_path.read_text(encoding="utf-8"))

    assert summary["dry_run"] is True
    assert sector_records == summary["sector_records"]
    assert parse_agent_card(sector_records).fingerprint == summary["card"]["manifest_fingerprint"]


def test_agent_card_inspect_command_outputs_json(tmp_path):
    sectors_path = tmp_path / "sectors.json"
    sectors_path.write_text(json.dumps(_valid_agent_card_records()), encoding="utf-8")
    stdout = StringIO()

    call_command("agent_card", "inspect", "--sectors-json", str(sectors_path), "--json", stdout=stdout)
    summary = json.loads(stdout.getvalue())

    assert len(summary["fingerprint"]) == 64
    assert summary["manifest"]["slot_code"] == "M"

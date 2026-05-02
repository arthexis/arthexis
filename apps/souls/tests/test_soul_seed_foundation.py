from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone

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
    activate_soul_seed_card,
    compose_skill_bundle,
    evict_card_session,
    evict_stale_card_sessions,
    provision_soul_seed_card,
    search_agent_skills,
)
from apps.souls.services.card_sessions import _active_console_sessions


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


def test_active_console_sessions_locks_only_session_rows():
    queryset = _active_console_sessions("console-a")

    assert queryset.query.select_for_update is True
    assert queryset.query.select_for_update_of == ("self",)


@pytest.mark.django_db
def test_activate_soul_seed_card_starts_bounded_console_session(skill):
    provision_soul_seed_card("rfid reader problem", card_uid="AABBCCDD", dry_run=False)

    summary = activate_soul_seed_card(
        "aa bb cc dd",
        console_id="console-a",
        reader_id="reader-1",
        trust_tier=CardSession.TrustTier.TRUSTED_OPERATOR_CONSOLE,
    )
    session = CardSession.objects.get(session_id=summary["session"]["session_id"])

    assert summary["action"] == "activated"
    assert session.state == CardSession.State.ACTIVE
    assert session.node_id == "console-a"
    assert session.reader_id == "reader-1"
    assert session.runtime_namespace == f"soul-seed:{session.session_id}:console-a"
    assert summary["card"]["card_uid"] == "AABBCCDD"
    assert summary["bundle"]["skill_slugs"] == [skill.slug]
    assert summary["interface"]["commands"] == ["suggest_next_action", "show_context"]
    assert summary["interface"]["suggestions"]


@pytest.mark.django_db
def test_card_session_allows_only_one_active_session_per_console():
    CardSession.objects.create(node_id="console-a", state=CardSession.State.ACTIVE)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CardSession.objects.create(node_id="console-a", state=CardSession.State.ACTIVE)

    CardSession.objects.create(node_id="console-a", state=CardSession.State.CLOSED)
    CardSession.objects.create(node_id="console-b", state=CardSession.State.ACTIVE)


@pytest.mark.django_db
def test_activate_soul_seed_card_retries_empty_console_insert_race(skill, monkeypatch):
    provision_soul_seed_card("rfid reader problem", card_uid="AABBCCDD", dry_run=False)
    original_save = CardSession.save
    calls = {"blocked": 0}

    def flaky_save(self, *args, **kwargs):
        if self.pk is None and self.state == CardSession.State.ACTIVE and calls["blocked"] == 0:
            calls["blocked"] += 1
            raise IntegrityError("simulated active console session race")
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(CardSession, "save", flaky_save)

    summary = activate_soul_seed_card("AABBCCDD", console_id="console-a")

    assert calls["blocked"] == 1
    assert summary["action"] == "activated"
    assert CardSession.objects.filter(node_id="console-a", state=CardSession.State.ACTIVE).count() == 1


@pytest.mark.django_db
def test_activate_soul_seed_card_rejects_missing_or_revoked_cards(skill):
    with pytest.raises(ValueError, match="No active Soul Seed card"):
        activate_soul_seed_card("AABBCCDD", console_id="console-a")

    provision = provision_soul_seed_card("rfid reader problem", card_uid="AABBCCDD", dry_run=False)
    SoulSeedCard.objects.filter(pk=provision["card"]["id"]).update(
        status=SoulSeedCard.Status.REVOKED,
        revoked_at=timezone.now(),
    )

    with pytest.raises(ValueError, match="No active Soul Seed card"):
        activate_soul_seed_card("AABBCCDD", console_id="console-a")


@pytest.mark.django_db
def test_activate_soul_seed_card_rejects_payload_fingerprint_mismatch(skill):
    provision = provision_soul_seed_card("rfid reader problem", card_uid="AABBCCDD", dry_run=False)
    SoulSeedCard.objects.filter(pk=provision["card"]["id"]).update(
        manifest_fingerprint="0" * 64,
    )

    with pytest.raises(ValueError, match="fingerprint does not match"):
        activate_soul_seed_card("AABBCCDD", console_id="console-a")


@pytest.mark.django_db
def test_activate_soul_seed_card_same_card_closes_existing_session(skill):
    provision_soul_seed_card("rfid reader problem", card_uid="AABBCCDD", dry_run=False)
    first = activate_soul_seed_card("AABBCCDD", console_id="console-a")

    second = activate_soul_seed_card("AABBCCDD", console_id="console-a")
    session = CardSession.objects.get(session_id=first["session"]["session_id"])

    assert second["action"] == "closed"
    assert second["session"]["session_id"] == first["session"]["session_id"]
    assert session.state == CardSession.State.CLOSED
    assert session.activation_plan == {}
    assert session.runtime_namespace == ""
    assert session.eviction_reason == "same card presented"


@pytest.mark.django_db
def test_activate_soul_seed_card_switch_evicts_previous_console_session(skill):
    provision_soul_seed_card("rfid reader problem", card_uid="AABBCCDD", dry_run=False)
    provision_soul_seed_card("rfid-triage", card_uid="11223344", dry_run=False)
    first = activate_soul_seed_card("AABBCCDD", console_id="console-a")

    second = activate_soul_seed_card("11223344", console_id="console-a")
    first_session = CardSession.objects.get(session_id=first["session"]["session_id"])
    second_session = CardSession.objects.get(session_id=second["session"]["session_id"])

    assert second["action"] == "activated"
    assert second["evicted_sessions"] == 1
    assert first_session.state == CardSession.State.EVICTED
    assert first_session.eviction_reason == "card switch"
    assert second_session.state == CardSession.State.ACTIVE
    assert second_session.card.card_uid == "11223344"


@pytest.mark.django_db
def test_activate_soul_seed_card_timeout_evicts_stale_session_before_start(skill):
    provision_soul_seed_card("rfid reader problem", card_uid="AABBCCDD", dry_run=False)
    provision_soul_seed_card("rfid-triage", card_uid="11223344", dry_run=False)
    first = activate_soul_seed_card("AABBCCDD", console_id="console-a")
    stale_at = timezone.now() - timedelta(minutes=10)
    CardSession.objects.filter(session_id=first["session"]["session_id"]).update(
        last_seen_at=stale_at,
    )

    second = activate_soul_seed_card("11223344", console_id="console-a", timeout_seconds=60)
    first_session = CardSession.objects.get(session_id=first["session"]["session_id"])

    assert second["evicted_sessions"] == 1
    assert first_session.state == CardSession.State.EVICTED
    assert first_session.eviction_reason == "timeout"


@pytest.mark.django_db
def test_evict_stale_card_sessions_evicts_by_console_timeout(skill):
    provision_soul_seed_card("rfid reader problem", card_uid="AABBCCDD", dry_run=False)
    active = activate_soul_seed_card("AABBCCDD", console_id="console-a")
    CardSession.objects.filter(session_id=active["session"]["session_id"]).update(
        last_seen_at=timezone.now() - timedelta(minutes=5),
    )

    evicted = evict_stale_card_sessions(console_id="console-a", timeout_seconds=60)
    session = CardSession.objects.get(session_id=active["session"]["session_id"])

    assert evicted == 1
    assert session.state == CardSession.State.EVICTED
    assert session.activation_plan == {}


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
def test_provision_soul_seed_card_write_matches_dry_run_fingerprint(skill):
    dry_run = provision_soul_seed_card(
        "rfid reader problem",
        card_uid="AABBCCDD",
        dry_run=True,
    )
    written = provision_soul_seed_card(
        "rfid reader problem",
        card_uid="AABBCCDD",
        dry_run=False,
    )

    assert written["sector_records"] == dry_run["sector_records"]
    assert written["card"]["manifest_fingerprint"] == dry_run["card"]["manifest_fingerprint"]


@pytest.mark.django_db
def test_provision_soul_seed_card_write_updates_active_card_for_duplicate_uid(skill):
    first = provision_soul_seed_card("rfid reader problem", card_uid="AABBCCDD", dry_run=False)
    second = provision_soul_seed_card("rfid-triage", card_uid="AABBCCDD", dry_run=False)

    assert second["card"]["created"] is False
    assert second["card"]["id"] == first["card"]["id"]
    assert SoulSeedCard.objects.filter(card_uid="AABBCCDD").count() == 1


@pytest.mark.django_db
def test_provision_soul_seed_card_preserves_owner_when_creator_missing(skill):
    user = get_user_model().objects.create_user(username="seed-owner")
    first = provision_soul_seed_card(
        "rfid reader problem",
        card_uid="AABBCCDD",
        created_by=user,
        dry_run=False,
    )
    second = provision_soul_seed_card("rfid-triage", card_uid="AABBCCDD", dry_run=False)

    assert second["card"]["id"] == first["card"]["id"]
    assert SoulSeedCard.objects.get(pk=second["card"]["id"]).owner == user


@pytest.mark.django_db
def test_provision_soul_seed_card_revokes_stale_duplicate_active_cards(skill):
    older = SoulSeedCard.objects.create(card_uid="AABBCCDD", status=SoulSeedCard.Status.ACTIVE)
    newer = SoulSeedCard.objects.create(card_uid="AABBCCDD", status=SoulSeedCard.Status.PREVIEW_ONLY)

    summary = provision_soul_seed_card("rfid reader problem", card_uid="AABBCCDD", dry_run=False)
    older.refresh_from_db()
    newer.refresh_from_db()

    assert summary["card"]["id"] == newer.pk
    assert newer.status == SoulSeedCard.Status.ACTIVE
    assert older.status == SoulSeedCard.Status.REVOKED
    assert older.revoked_at is not None


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


@pytest.mark.django_db
def test_soul_seed_activate_command_outputs_json_from_scan_payload(skill, tmp_path):
    provision_soul_seed_card("rfid reader problem", card_uid="AABBCCDD", dry_run=False)
    scan_path = tmp_path / "scan.json"
    scan_path.write_text(json.dumps({"rfid": "aa bb cc dd"}), encoding="utf-8")
    stdout = StringIO()

    call_command(
        "soul_seed",
        "activate",
        "--scan-json",
        str(scan_path),
        "--console-id",
        "console-a",
        "--reader-id",
        "reader-1",
        "--json",
        stdout=stdout,
    )
    summary = json.loads(stdout.getvalue())

    assert summary["action"] == "activated"
    assert summary["card"]["card_uid"] == "AABBCCDD"
    assert summary["session"]["console_id"] == "console-a"
    assert summary["interface"]["visible_fields"] == ["intent", "matches", "commands", "suggestions"]


@pytest.mark.django_db
def test_soul_seed_evict_stale_command_outputs_json(skill):
    provision_soul_seed_card("rfid reader problem", card_uid="AABBCCDD", dry_run=False)
    active = activate_soul_seed_card("AABBCCDD", console_id="console-a")
    CardSession.objects.filter(session_id=active["session"]["session_id"]).update(
        last_seen_at=timezone.now() - timedelta(minutes=5),
    )
    stdout = StringIO()

    call_command(
        "soul_seed",
        "evict-stale",
        "--console-id",
        "console-a",
        "--timeout-seconds",
        "60",
        "--json",
        stdout=stdout,
    )
    summary = json.loads(stdout.getvalue())

    assert summary == {"action": "evict-stale", "evicted_sessions": 1}


def test_agent_card_inspect_command_outputs_json(tmp_path):
    sectors_path = tmp_path / "sectors.json"
    sectors_path.write_text(json.dumps(_valid_agent_card_records()), encoding="utf-8")
    stdout = StringIO()

    call_command("agent_card", "inspect", "--sectors-json", str(sectors_path), "--json", stdout=stdout)
    summary = json.loads(stdout.getvalue())

    assert len(summary["fingerprint"]) == 64
    assert summary["manifest"]["slot_code"] == "M"

from __future__ import annotations

import re
from typing import Any

from django.contrib.auth import get_user_model
from django.db import transaction

from apps.cards.agent_card import build_agent_card_sector_payloads
from apps.cards.models import RFID
from apps.souls.models import AgentInterfaceSpec, SkillBundle, SoulSeedCard
from apps.souls.services.skill_matching import compose_skill_bundle

CARD_UID_RE = re.compile(r"^[0-9A-F]+$")


def normalize_card_uid(card_uid: str) -> str:
    normalized = RFID.normalize_code(card_uid)
    if not normalized:
        raise ValueError("card_uid is required.")
    if not CARD_UID_RE.fullmatch(normalized):
        raise ValueError("card_uid must contain hexadecimal digits only.")
    return normalized


def _validated_created_by(created_by):
    if created_by is None:
        return None
    user_model = get_user_model()
    if isinstance(created_by, user_model):
        return created_by
    return None


def _identity_sources(
    *,
    card_uid: str,
    bundle_result: dict[str, Any],
    bundle: SkillBundle | None = None,
    interface_spec: AgentInterfaceSpec | None = None,
) -> dict[str, object]:
    intent_id = bundle.intent_id if bundle else bundle_result["intent"].get("id")
    bundle_id = bundle.pk if bundle else bundle_result["bundle"].get("id")
    interface_id = (
        interface_spec.pk
        if interface_spec
        else bundle_result["interface_spec"].get("id")
    )
    return {
        "intent": intent_id or bundle_result["intent"].get("normalized_intent"),
        "bundle": bundle_id or bundle_result["bundle"].get("slug"),
        "interface": interface_id or bundle_result["interface_spec"]["schema"],
        "card": card_uid,
    }


def _build_payload(
    *,
    card_uid: str,
    bundle_result: dict[str, Any],
    bundle: SkillBundle | None = None,
    interface_spec: AgentInterfaceSpec | None = None,
) -> dict[str, Any]:
    skill_slugs = bundle_result["bundle"].get("skill_slugs", [])
    build_result = build_agent_card_sector_payloads(
        identity_sources=_identity_sources(
            card_uid=card_uid,
            bundle_result=bundle_result,
            bundle=bundle,
            interface_spec=interface_spec,
        ),
        skill_slugs=skill_slugs,
    )
    return build_result.to_dict()


def _update_bundle_compatibility_notes(
    bundle: SkillBundle,
    notes: list[str],
) -> None:
    if not notes:
        return
    existing_notes = list(bundle.compatibility_notes or [])
    merged_notes = [*existing_notes]
    for note in notes:
        if note not in merged_notes:
            merged_notes.append(note)
    if merged_notes == existing_notes:
        return
    bundle.compatibility_notes = merged_notes
    bundle.save(update_fields=["compatibility_notes", "updated_at"])


def _card_payload_summary(
    *,
    dry_run: bool,
    card_uid: str,
    bundle_result: dict[str, Any],
    agent_card: dict[str, Any],
    card: SoulSeedCard | None = None,
    created: bool | None = None,
) -> dict[str, Any]:
    result = {
        "dry_run": dry_run,
        "card": {
            "id": card.pk if card else None,
            "card_uid": card_uid,
            "status": card.status if card else SoulSeedCard.Status.ACTIVE,
            "created": created,
            "manifest_fingerprint": agent_card["fingerprint"],
        },
        "intent": bundle_result["intent"],
        "bundle": bundle_result["bundle"],
        "interface_spec": bundle_result["interface_spec"],
        "matches": bundle_result.get("matches", []),
        "agent_card": agent_card,
        "sector_records": agent_card["sector_records"],
        "padded_sector_records": agent_card["padded_sector_records"],
        "compatibility_notes": agent_card["compatibility_notes"],
        "omitted_skill_sigils": agent_card["omitted_skill_sigils"],
    }
    if card and card.rfid_id:
        result["card"]["rfid_label_id"] = card.rfid_id
    return result


def provision_soul_seed_card(
    prompt: str,
    *,
    card_uid: str,
    created_by=None,
    limit: int = 5,
    dry_run: bool = True,
) -> dict[str, Any]:
    normalized_card_uid = normalize_card_uid(card_uid)
    created_by = _validated_created_by(created_by)
    if dry_run:
        bundle_result = compose_skill_bundle(
            prompt,
            created_by=created_by,
            limit=limit,
            dry_run=True,
        )
        agent_card = _build_payload(
            card_uid=normalized_card_uid,
            bundle_result=bundle_result,
        )
        return _card_payload_summary(
            dry_run=True,
            card_uid=normalized_card_uid,
            bundle_result=bundle_result,
            agent_card=agent_card,
        )

    with transaction.atomic():
        bundle_result = compose_skill_bundle(
            prompt,
            created_by=created_by,
            limit=limit,
            dry_run=False,
        )
        bundle = (
            SkillBundle.objects.prefetch_related("skills")
            .select_related("intent", "primary_skill")
            .get(pk=bundle_result["bundle"]["id"])
        )
        interface_spec = AgentInterfaceSpec.objects.get(pk=bundle_result["interface_spec"]["id"])
        agent_card = _build_payload(
            card_uid=normalized_card_uid,
            bundle_result=bundle_result,
            bundle=bundle,
            interface_spec=interface_spec,
        )
        _update_bundle_compatibility_notes(bundle, agent_card["compatibility_notes"])
        rfid, _rfid_created = RFID.update_or_create_from_code(normalized_card_uid)
        card = (
            SoulSeedCard.objects.exclude(status=SoulSeedCard.Status.REVOKED)
            .filter(card_uid=normalized_card_uid)
            .order_by("-id")
            .first()
        )
        created = card is None
        if card is None:
            card = SoulSeedCard(card_uid=normalized_card_uid)
        card.rfid = rfid
        card.intent = bundle.intent
        card.skill_bundle = bundle
        card.interface_spec = interface_spec
        card.owner = created_by
        card.status = SoulSeedCard.Status.ACTIVE
        card.manifest_fingerprint = agent_card["fingerprint"]
        card.card_payload = {
            "agent_card": agent_card,
            "prompt": prompt,
            "bundle": bundle_result["bundle"],
            "interface_spec": bundle_result["interface_spec"],
        }
        card.save()
    return _card_payload_summary(
        dry_run=False,
        card_uid=normalized_card_uid,
        bundle_result=bundle_result,
        agent_card=agent_card,
        card=card,
        created=created,
    )


def plan_soul_seed_card(
    prompt: str,
    *,
    card_uid: str,
    created_by=None,
    limit: int = 5,
) -> dict[str, Any]:
    return provision_soul_seed_card(
        prompt,
        card_uid=card_uid,
        created_by=created_by,
        limit=limit,
        dry_run=True,
    )

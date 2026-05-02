from __future__ import annotations

import json
import re
from typing import Any

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

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


def _stable_identity_value(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _identity_sources(
    *,
    card_uid: str,
    bundle_result: dict[str, Any],
) -> dict[str, object]:
    bundle_identity = {
        "match_score": bundle_result["bundle"].get("match_score"),
        "match_strategy": bundle_result["bundle"].get("match_strategy"),
        "primary_skill": bundle_result["bundle"].get("primary_skill"),
        "skill_slugs": bundle_result["bundle"].get("skill_slugs", []),
        "summary": bundle_result["bundle"].get("summary"),
    }
    interface_identity = {
        "commands": bundle_result["interface_spec"].get("commands", []),
        "schema": bundle_result["interface_spec"].get("schema", {}),
        "visible_fields": bundle_result["interface_spec"].get("visible_fields", []),
    }
    return {
        "intent": bundle_result["intent"].get("normalized_intent"),
        "bundle": _stable_identity_value(bundle_identity),
        "interface": _stable_identity_value(interface_identity),
        "card": card_uid,
    }


def _build_payload(
    *,
    card_uid: str,
    bundle_result: dict[str, Any],
) -> dict[str, Any]:
    skill_slugs = bundle_result["bundle"].get("skill_slugs", [])
    build_result = build_agent_card_sector_payloads(
        identity_sources=_identity_sources(
            card_uid=card_uid,
            bundle_result=bundle_result,
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


def _locked_rfid_for_uid(card_uid: str) -> RFID:
    try:
        with transaction.atomic():
            rfid, _created = RFID.update_or_create_from_code(card_uid)
    except IntegrityError:
        rfid = RFID.find_match(card_uid)
        if rfid is None:
            raise
    # The RFID row is the per-card UID mutex, including the no-SoulSeedCard-yet case.
    return RFID.objects.select_for_update().get(pk=rfid.pk)


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
        agent_card = _build_payload(card_uid=normalized_card_uid, bundle_result=bundle_result)
        _update_bundle_compatibility_notes(bundle, agent_card["compatibility_notes"])
        rfid = _locked_rfid_for_uid(normalized_card_uid)
        active_cards = list(
            SoulSeedCard.objects.select_for_update()
            .exclude(status=SoulSeedCard.Status.REVOKED)
            .filter(card_uid=normalized_card_uid)
            .order_by("-id")
        )
        card = active_cards[0] if active_cards else None
        stale_card_ids = [existing_card.pk for existing_card in active_cards[1:]]
        if stale_card_ids:
            SoulSeedCard.objects.filter(pk__in=stale_card_ids).update(
                status=SoulSeedCard.Status.REVOKED,
                revoked_at=timezone.now(),
            )
        created = card is None
        if card is None:
            card = SoulSeedCard(card_uid=normalized_card_uid)
        card.rfid = rfid
        card.intent = bundle.intent
        card.skill_bundle = bundle
        card.interface_spec = interface_spec
        if created_by is not None or card.owner_id is None:
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

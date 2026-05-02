from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.cards.agent_card import AgentCardError, parse_agent_card
from apps.souls.models import CardSession, SoulSeedCard
from apps.souls.services.card_provisioning import normalize_card_uid

ACTIVE_SESSION_STATES = (CardSession.State.ACTIVE,)
DEFAULT_SWITCH_REASON = "card switch"
DEFAULT_TIMEOUT_REASON = "timeout"
SAME_CARD_REASON = "same card presented"


def _console_id(value: str) -> str:
    console_id = str(value or "").strip()
    if not console_id:
        raise ValueError("console_id is required.")
    return console_id[:128]


def _runtime_namespace(console_id: str, session_id: str) -> str:
    safe_console = re.sub(r"[^A-Za-z0-9_.:-]+", "-", console_id).strip("-") or "console"
    return f"soul-seed:{safe_console}:{session_id}"[:128]


def evict_card_session(session: CardSession, *, reason: str = "") -> CardSession:
    """Mark a card session as evicted and drop runtime activation details."""

    session.state = CardSession.State.EVICTED
    session.ended_at = timezone.now()
    session.last_seen_at = session.ended_at
    session.eviction_reason = reason[:255]
    session.activation_plan = {}
    session.runtime_namespace = ""
    session.save(
        update_fields=[
            "state",
            "ended_at",
            "last_seen_at",
            "eviction_reason",
            "activation_plan",
            "runtime_namespace",
        ]
    )
    return session


def close_card_session(session: CardSession, *, reason: str = "") -> CardSession:
    """Close a card session and drop runtime activation details."""

    session.state = CardSession.State.CLOSED
    session.ended_at = timezone.now()
    session.last_seen_at = session.ended_at
    session.eviction_reason = reason[:255]
    session.activation_plan = {}
    session.runtime_namespace = ""
    session.save(
        update_fields=[
            "state",
            "ended_at",
            "last_seen_at",
            "eviction_reason",
            "activation_plan",
            "runtime_namespace",
        ]
    )
    return session


def _active_console_sessions(console_id: str):
    return (
        CardSession.objects.select_for_update()
        .filter(node_id=console_id, state__in=ACTIVE_SESSION_STATES)
        .select_related(
            "card",
            "card__intent",
            "card__skill_bundle",
            "card__interface_spec",
            "rfid",
        )
        .order_by("-last_seen_at", "-id")
    )


def _resolve_active_card(card_uid: str) -> SoulSeedCard:
    normalized_card_uid = normalize_card_uid(card_uid)
    card = (
        SoulSeedCard.objects.select_related("rfid", "intent", "skill_bundle", "interface_spec")
        .filter(card_uid=normalized_card_uid, status=SoulSeedCard.Status.ACTIVE)
        .order_by("-id")
        .first()
    )
    if card is None:
        raise ValueError(f"No active Soul Seed card found for UID {normalized_card_uid}.")
    if card.rfid_id is None:
        raise ValueError(f"Soul Seed card {normalized_card_uid} is missing an RFID registry link.")
    if card.skill_bundle_id is None or card.interface_spec_id is None:
        raise ValueError(f"Soul Seed card {normalized_card_uid} is missing activation metadata.")
    _validate_agent_card_payload(card)
    return card


def _validate_agent_card_payload(card: SoulSeedCard) -> None:
    agent_card = (card.card_payload or {}).get("agent_card") or {}
    sector_records = agent_card.get("sector_records")
    if not sector_records:
        raise ValueError(f"Soul Seed card {card.card_uid} is missing Agent Card sectors.")
    try:
        parsed = parse_agent_card(sector_records)
    except AgentCardError as error:
        raise ValueError(f"Soul Seed card {card.card_uid} has invalid Agent Card payload: {error}") from error
    expected_fingerprint = card.manifest_fingerprint or agent_card.get("fingerprint")
    if expected_fingerprint and parsed.fingerprint != expected_fingerprint:
        raise ValueError(f"Soul Seed card {card.card_uid} fingerprint does not match its payload.")


def _interface_payload(card: SoulSeedCard) -> dict[str, Any]:
    interface_spec = card.interface_spec
    bundle = card.skill_bundle
    skill_slugs: list[str] = []
    if bundle is not None:
        skill_slugs = list(bundle.skills.order_by("slug").values_list("slug", flat=True))
    return {
        "card": {
            "id": card.pk,
            "card_uid": card.card_uid,
            "manifest_fingerprint": card.manifest_fingerprint,
            "rfid_label_id": card.rfid_id,
        },
        "intent": {
            "id": card.intent_id,
            "normalized_intent": card.intent.normalized_intent if card.intent else "",
            "problem_statement": card.intent.problem_statement if card.intent else "",
            "role": card.intent.role if card.intent else "",
            "risk_level": card.intent.risk_level if card.intent else "",
        },
        "bundle": {
            "id": bundle.pk if bundle else None,
            "slug": bundle.slug if bundle else "",
            "name": bundle.name if bundle else "",
            "summary": bundle.summary if bundle else "",
            "match_strategy": bundle.match_strategy if bundle else "",
            "match_score": bundle.match_score if bundle else None,
            "primary_skill": bundle.primary_skill.slug if bundle and bundle.primary_skill else "",
            "skill_slugs": skill_slugs,
            "tool_allowlist": bundle.tool_allowlist if bundle else [],
            "compatibility_notes": bundle.compatibility_notes if bundle else [],
            "fallback_guidance": bundle.fallback_guidance if bundle else "",
        },
        "interface": {
            "id": interface_spec.pk if interface_spec else None,
            "mode": interface_spec.mode if interface_spec else "",
            "schema": interface_spec.schema if interface_spec else {},
            "commands": interface_spec.commands if interface_spec else [],
            "suggestions": interface_spec.suggestions if interface_spec else [],
            "visible_fields": interface_spec.visible_fields if interface_spec else [],
        },
    }


def _session_payload(session: CardSession, *, action: str, evicted: int = 0) -> dict[str, Any]:
    payload = {
        "action": action,
        "evicted_sessions": evicted,
        "session": {
            "id": session.pk,
            "session_id": session.session_id,
            "state": session.state,
            "console_id": session.node_id,
            "reader_id": session.reader_id,
            "trust_tier": session.trust_tier,
            "runtime_namespace": session.runtime_namespace,
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "ended_at": session.ended_at.isoformat() if session.ended_at else None,
            "last_seen_at": session.last_seen_at.isoformat() if session.last_seen_at else None,
            "eviction_reason": session.eviction_reason,
        },
    }
    if session.activation_plan:
        payload.update(session.activation_plan)
    elif session.card_id:
        payload["card"] = {
            "id": session.card_id,
            "card_uid": session.card.card_uid if session.card else "",
        }
    return payload


def _evict_stale_sessions_locked(*, console_id: str, timeout_seconds: int | None) -> int:
    if timeout_seconds is None or timeout_seconds <= 0:
        return 0
    cutoff = timezone.now() - timedelta(seconds=timeout_seconds)
    evicted = 0
    for session in _active_console_sessions(console_id).filter(last_seen_at__lt=cutoff):
        evict_card_session(session, reason=DEFAULT_TIMEOUT_REASON)
        evicted += 1
    return evicted


def activate_soul_seed_card(
    card_uid: str,
    *,
    console_id: str,
    reader_id: str = "",
    trust_tier: str = CardSession.TrustTier.UNKNOWN,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Activate, close, or switch the active Soul Seed session for a console."""

    normalized_console_id = _console_id(console_id)
    normalized_reader_id = str(reader_id or "").strip()[:128]
    normalized_card_uid = normalize_card_uid(card_uid)
    if trust_tier not in CardSession.TrustTier.values:
        raise ValueError(f"Unsupported trust tier: {trust_tier}")

    with transaction.atomic():
        evicted_count = _evict_stale_sessions_locked(
            console_id=normalized_console_id,
            timeout_seconds=timeout_seconds,
        )
        active_sessions = list(_active_console_sessions(normalized_console_id))
        current_session = active_sessions[0] if active_sessions else None
        if current_session and current_session.card and current_session.card.card_uid == normalized_card_uid:
            close_card_session(current_session, reason=SAME_CARD_REASON)
            return _session_payload(current_session, action="closed", evicted=evicted_count)

        card = _resolve_active_card(normalized_card_uid)
        for session in active_sessions:
            evict_card_session(session, reason=DEFAULT_SWITCH_REASON)
            evicted_count += 1

        started_at = timezone.now()
        session = CardSession.objects.create(
            card=card,
            rfid=card.rfid,
            reader_id=normalized_reader_id,
            node_id=normalized_console_id,
            trust_tier=trust_tier,
            state=CardSession.State.ACTIVE,
            started_at=started_at,
            last_seen_at=started_at,
        )
        session.runtime_namespace = _runtime_namespace(normalized_console_id, session.session_id)
        session.activation_plan = _interface_payload(card)
        session.save(update_fields=["runtime_namespace", "activation_plan", "updated_at"])
        return _session_payload(session, action="activated", evicted=evicted_count)


def evict_stale_card_sessions(
    *,
    console_id: str,
    timeout_seconds: int,
    reason: str = DEFAULT_TIMEOUT_REASON,
) -> int:
    """Evict active card sessions for a console that have exceeded timeout_seconds."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive.")
    normalized_console_id = _console_id(console_id)
    cutoff = timezone.now() - timedelta(seconds=timeout_seconds)
    evicted = 0
    with transaction.atomic():
        for session in _active_console_sessions(normalized_console_id).filter(last_seen_at__lt=cutoff):
            evict_card_session(session, reason=reason)
            evicted += 1
    return evicted

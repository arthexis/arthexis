from __future__ import annotations

from django.utils import timezone

from apps.souls.models import CardSession


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

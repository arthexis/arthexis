"""Read operator-facing summaries of durable OCPP compatibility evidence."""

from __future__ import annotations

from collections.abc import Iterable

from django.db.models import Count, Q

from apps.ocpp.models import CompatibilityEvidence


def query_compatibility_diagnostics(
    *,
    charger_identity: str | None = None,
    kinds: Iterable[str] = (),
    limit: int = 50,
) -> dict[str, object]:
    """Return bounded recent compatibility evidence and aggregate counts."""

    if limit <= 0:
        raise ValueError("compatibility diagnostics limit must be positive")

    selected_kinds = tuple(dict.fromkeys(kind.strip() for kind in kinds if kind.strip()))
    evidence = CompatibilityEvidence.objects.all()

    if charger_identity:
        evidence = evidence.filter(
            Q(charger_identity=charger_identity)
            | Q(charger__identity=charger_identity)
        )
    if selected_kinds:
        evidence = evidence.filter(kind__in=selected_kinds)

    counts = {
        row["kind"]: row["count"]
        for row in evidence.values("kind")
        .annotate(count=Count("id"))
        .order_by("kind")
    }
    total = sum(counts.values())
    recent = list(
        evidence.select_related("charger")
        .order_by("-observed_at", "-id")[:limit]
    )

    return {
        "charger": charger_identity or None,
        "kinds": list(selected_kinds),
        "total": total,
        "counts": counts,
        "latest_observed_at": (
            recent[0].observed_at.isoformat() if recent else None
        ),
        "events": [_serialize(item) for item in recent],
    }


def _serialize(evidence: CompatibilityEvidence) -> dict[str, object]:
    return {
        "observed_at": evidence.observed_at.isoformat(),
        "kind": evidence.kind,
        "charger": (
            evidence.charger_identity
            or (evidence.charger.identity if evidence.charger_id else "")
        ),
        "protocol": evidence.protocol,
        "unique_id": evidence.unique_id,
        "action": evidence.action,
        "details": evidence.details,
    }

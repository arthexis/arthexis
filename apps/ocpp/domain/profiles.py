"""Charging-profile persistence service."""

from apps.ocpp.models import Charger, ChargingProfile


def record_profile(
    *,
    charger: Charger,
    remote_id: str,
    purpose: str,
    kind: str,
    payload: dict[str, object],
    stack_level: int = 0,
) -> ChargingProfile:
    """Upsert one charging profile or charging-limit payload."""
    profile, _ = ChargingProfile.objects.update_or_create(
        charger=charger,
        remote_id=remote_id,
        defaults={
            "purpose": purpose,
            "kind": kind,
            "payload": payload,
            "stack_level": stack_level,
            "active": True,
        },
    )
    return profile

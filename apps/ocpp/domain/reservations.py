"""Reservation persistence service."""

from datetime import datetime

from apps.ocpp.models import Charger, Connector, Reservation


def record_reservation(
    *,
    charger: Charger,
    remote_id: str,
    id_tag: str,
    expires_at: datetime,
    connector: Connector | None = None,
    status: str = "pending",
) -> Reservation:
    """Upsert a retained reservation from either OCPP version."""
    reservation, _ = Reservation.objects.update_or_create(
        remote_id=remote_id,
        defaults={
            "charger": charger,
            "connector": connector,
            "id_tag": id_tag,
            "expires_at": expires_at,
            "status": status,
        },
    )
    return reservation

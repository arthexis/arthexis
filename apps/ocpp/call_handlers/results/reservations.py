"""Call result handlers for reservation actions."""
from __future__ import annotations

from channels.db import database_sync_to_async
from django.utils import timezone

from ... import store
from ...models import CPReservation
from ..types import CallResultContext


async def handle_reserve_now_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "ReserveNow result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")

    reservation_pk = metadata.get("reservation_pk")

    def _apply():
        if not reservation_pk:
            return
        reservation = CPReservation.objects.filter(pk=reservation_pk).first()
        if not reservation:
            return
        reservation.evcs_status = status_value
        reservation.evcs_error = ""
        confirmed = status_value.casefold() == "accepted"
        reservation.evcs_confirmed = confirmed
        reservation.evcs_confirmed_at = timezone.now() if confirmed else None
        reservation.save(
            update_fields=[
                "evcs_status",
                "evcs_error",
                "evcs_confirmed",
                "evcs_confirmed_at",
                "updated_on",
            ]
        )

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_cancel_reservation_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "CancelReservation result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")

    reservation_pk = metadata.get("reservation_pk")

    def _apply():
        if not reservation_pk:
            return
        reservation = CPReservation.objects.filter(pk=reservation_pk).first()
        if not reservation:
            return
        reservation.evcs_status = status_value
        reservation.evcs_error = ""
        reservation.evcs_confirmed = False
        reservation.evcs_confirmed_at = None
        reservation.save(
            update_fields=[
                "evcs_status",
                "evcs_error",
                "evcs_confirmed",
                "evcs_confirmed_at",
                "updated_on",
            ]
        )

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True

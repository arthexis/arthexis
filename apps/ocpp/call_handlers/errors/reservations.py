"""Call error handlers for reservation actions."""
from __future__ import annotations

from channels.db import database_sync_to_async

from ... import store
from ...models import CPReservation
from ..types import CallErrorContext
from ..utils import _json_details


async def handle_reserve_now_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    parts: list[str] = []
    code_text = (error_code or "").strip() if error_code else ""
    if code_text:
        parts.append(f"code={code_text}")
    description_text = (description or "").strip() if description else ""
    if description_text:
        parts.append(f"description={description_text}")
    details_text = _json_details(details)
    if details_text:
        parts.append(f"details={details_text}")
    message = "ReserveNow error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")

    reservation_pk = metadata.get("reservation_pk")

    def _apply():
        if not reservation_pk:
            return
        reservation = CPReservation.objects.filter(pk=reservation_pk).first()
        if not reservation:
            return
        summary_parts = []
        if code_text:
            summary_parts.append(code_text)
        if description_text:
            summary_parts.append(description_text)
        if details_text:
            summary_parts.append(details_text)
        reservation.evcs_status = ""
        reservation.evcs_error = "; ".join(summary_parts)
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
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_cancel_reservation_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    parts: list[str] = []
    code_text = (error_code or "").strip() if error_code else ""
    if code_text:
        parts.append(f"code={code_text}")
    description_text = (description or "").strip() if description else ""
    if description_text:
        parts.append(f"description={description_text}")
    details_text = _json_details(details)
    if details_text:
        parts.append(f"details={details_text}")
    message = "CancelReservation error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")

    reservation_pk = metadata.get("reservation_pk")

    def _apply():
        if not reservation_pk:
            return
        reservation = CPReservation.objects.filter(pk=reservation_pk).first()
        if not reservation:
            return
        summary_parts = []
        if code_text:
            summary_parts.append(code_text)
        if description_text:
            summary_parts.append(description_text)
        if details_text:
            summary_parts.append(details_text)
        reservation.evcs_status = ""
        reservation.evcs_error = "; ".join(summary_parts)
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
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True

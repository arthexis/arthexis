"""Call error handlers for availability actions."""
from __future__ import annotations

from ... import store
from ..types import CallErrorContext
from ..utils import _json_details


async def handle_change_availability_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    detail_text = _json_details(details) if details is not None else ""
    if not detail_text:
        detail_text = (description or "").strip()
    if not detail_text:
        detail_text = (error_code or "").strip() or "Error"
    requested_type = metadata.get("availability_type")
    connector_value = metadata.get("connector_id")
    requested_at = metadata.get("requested_at")
    await consumer._update_change_availability_state(
        connector_value,
        requested_type,
        "Rejected",
        requested_at,
        details=detail_text,
    )
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_unlock_connector_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    detail_text = _json_details(details) if details is not None else ""
    if not detail_text:
        detail_text = (description or "").strip()
    if not detail_text:
        detail_text = (error_code or "").strip() or "Error"

    connector_value = metadata.get("connector_id")
    requested_at = metadata.get("requested_at")
    await consumer._update_change_availability_state(
        connector_value,
        None,
        "Rejected",
        requested_at,
        details=detail_text,
    )

    parts: list[str] = []
    if error_code:
        parts.append(f"code={error_code}")
    if description:
        parts.append(f"description={description}")
    if details:
        parts.append(f"details={_json_details(details)}")
    message = "UnlockConnector error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")

    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True

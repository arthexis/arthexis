"""Call result handlers for availability actions."""
from __future__ import annotations

from ... import store
from ..types import CallResultContext
from ..utils import _format_status_info


async def handle_change_availability_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status = str((payload_data or {}).get("status") or "").strip()
    requested_type = metadata.get("availability_type")
    connector_value = metadata.get("connector_id")
    requested_at = metadata.get("requested_at")
    await consumer._update_change_availability_state(
        connector_value,
        requested_type,
        status,
        requested_at,
        details="",
    )
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_unlock_connector_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str((payload_data or {}).get("status") or "").strip()
    status_info_text = _format_status_info((payload_data or {}).get("statusInfo"))
    connector_value = metadata.get("connector_id")
    requested_at = metadata.get("requested_at")

    await consumer._update_change_availability_state(
        connector_value,
        None,
        status_value,
        requested_at,
        details=status_info_text,
    )

    result_metadata = dict(metadata or {})
    if status_value:
        result_metadata["status"] = status_value
    if status_info_text:
        result_metadata["status_info"] = status_info_text

    store.record_pending_call_result(
        message_id,
        metadata=result_metadata,
        payload=payload_data,
    )
    return True

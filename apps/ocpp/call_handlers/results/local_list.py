"""Call result handlers for local list actions."""
from __future__ import annotations

from ... import store
from ..types import CallResultContext


async def handle_send_local_list_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    version_candidate = (
        payload_data.get("currentLocalListVersion")
        or payload_data.get("listVersion")
        or metadata.get("list_version")
    )
    message = "SendLocalList result"
    if status_value:
        message += f": status={status_value}"
    if version_candidate is not None:
        message += f", version={version_candidate}"
    store.add_log(log_key, message, log_type="charger")
    version_int = None
    if version_candidate is not None:
        try:
            version_int = int(version_candidate)
        except (TypeError, ValueError):
            version_int = None
    await consumer._update_local_authorization_state(version_int)
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_get_local_list_version_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    version_candidate = payload_data.get("listVersion")
    processed = 0
    auth_list = payload_data.get("localAuthorizationList")
    if isinstance(auth_list, list):
        processed = await consumer._apply_local_authorization_entries(auth_list)
    message = "GetLocalListVersion result"
    if version_candidate is not None:
        message += f": version={version_candidate}"
    if processed:
        message += f", entries={processed}"
    store.add_log(log_key, message, log_type="charger")
    version_int = None
    if version_candidate is not None:
        try:
            version_int = int(version_candidate)
        except (TypeError, ValueError):
            version_int = None
    await consumer._update_local_authorization_state(version_int)
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_clear_cache_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "ClearCache result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    version_int = 0 if status_value == "Accepted" else None
    await consumer._update_local_authorization_state(version_int)
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True

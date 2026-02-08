"""Call result handlers for log actions."""
from __future__ import annotations

import base64

from channels.db import database_sync_to_async
from django.utils import timezone

from ... import store
from ...models import ChargerLogRequest
from ..types import CallResultContext


async def handle_get_log_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    request_pk = metadata.get("log_request_pk")
    capture_key = metadata.get("capture_key")
    status_value = str(payload_data.get("status") or "").strip()
    filename_value = str(payload_data.get("filename") or payload_data.get("location") or "").strip()
    location_value = str(payload_data.get("location") or "").strip()
    fragments: list[str] = []
    data_candidate = payload_data.get("logData") or payload_data.get("entries")
    if isinstance(data_candidate, (list, tuple)):
        for entry in data_candidate:
            if entry is None:
                continue
            if isinstance(entry, (bytes, bytearray)):
                try:
                    fragments.append(entry.decode("utf-8"))
                except Exception:
                    fragments.append(base64.b64encode(entry).decode("ascii"))
            else:
                fragments.append(str(entry))
    elif data_candidate not in (None, ""):
        fragments.append(str(data_candidate))

    def _update_request() -> str:
        request = None
        if request_pk:
            request = ChargerLogRequest.objects.filter(pk=request_pk).first()
        if request is None:
            return ""
        updates: dict[str, object] = {
            "responded_at": timezone.now(),
            "raw_response": payload_data,
        }
        if status_value:
            updates["status"] = status_value
        if filename_value:
            updates["filename"] = filename_value
        if location_value:
            updates["location"] = location_value
        if capture_key:
            updates["session_key"] = str(capture_key)
        message_identifier = metadata.get("message_id")
        if message_identifier:
            updates["message_id"] = str(message_identifier)
        ChargerLogRequest.objects.filter(pk=request.pk).update(**updates)
        for field, value in updates.items():
            setattr(request, field, value)
        return request.session_key or ""

    session_capture = await database_sync_to_async(_update_request)()
    message = "GetLog result"
    if status_value:
        message += f": status={status_value}"
    if filename_value:
        message += f", filename={filename_value}"
    if location_value:
        message += f", location={location_value}"
    store.add_log(log_key, message, log_type="charger")
    if capture_key and fragments:
        for fragment in fragments:
            store.append_log_capture(str(capture_key), fragment)
        store.finalize_log_capture(str(capture_key))
    elif session_capture and status_value.lower() in {
        "uploaded",
        "uploadfailure",
        "rejected",
        "idle",
    }:
        store.finalize_log_capture(session_capture)
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True

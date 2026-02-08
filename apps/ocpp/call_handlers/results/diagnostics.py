"""Call result handlers for diagnostics actions."""
from __future__ import annotations

from channels.db import database_sync_to_async
from django.utils import timezone

from ... import store
from ...models import Charger
from ..types import CallResultContext


async def handle_get_diagnostics_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    file_name = str(payload_data.get("fileName") or payload_data.get("filename") or "").strip()
    location_value = str(payload_data.get("location") or metadata.get("location") or "").strip()
    message = "GetDiagnostics result"
    if status_value:
        message += f": status={status_value}"
    if file_name:
        message += f", fileName={file_name}"
    if location_value:
        message += f", location={location_value}"
    store.add_log(log_key, message, log_type="charger")

    def _apply_updates():
        charger_id = metadata.get("charger_id")
        if not charger_id:
            return
        updates: dict[str, object] = {"diagnostics_timestamp": timezone.now()}
        if location_value:
            updates["diagnostics_location"] = location_value
        elif file_name:
            updates["diagnostics_location"] = file_name
        Charger.objects.filter(charger_id=charger_id).update(**updates)

    await database_sync_to_async(_apply_updates)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True

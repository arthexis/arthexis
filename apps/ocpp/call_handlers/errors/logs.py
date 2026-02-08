"""Call error handlers for log actions."""
from __future__ import annotations

from channels.db import database_sync_to_async
from django.utils import timezone

from ... import store
from ...models import ChargerLogRequest
from ..types import CallErrorContext


async def handle_get_log_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    request_pk = metadata.get("log_request_pk")
    capture_key = metadata.get("capture_key")

    def _apply_error() -> None:
        if not request_pk:
            return
        request = ChargerLogRequest.objects.filter(pk=request_pk).first()
        if not request:
            return
        label = (error_code or "Error").strip() or "Error"
        request.status = label
        request.responded_at = timezone.now()
        request.raw_response = {
            "errorCode": error_code,
            "errorDescription": description,
            "details": details,
        }
        if capture_key:
            request.session_key = str(capture_key)
        request.save(
            update_fields=[
                "status",
                "responded_at",
                "raw_response",
                "session_key",
            ]
        )

    await database_sync_to_async(_apply_error)()
    parts: list[str] = []
    if error_code:
        parts.append(f"code={error_code}")
    if description:
        parts.append(f"description={description}")
    message = "GetLog error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    if capture_key:
        store.finalize_log_capture(str(capture_key))
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True

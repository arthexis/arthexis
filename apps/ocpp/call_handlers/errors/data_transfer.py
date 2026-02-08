"""Call error handlers for data transfer actions."""
from __future__ import annotations

from channels.db import database_sync_to_async
from django.utils import timezone

from ... import store
from ...models import DataTransferMessage
from ..types import CallErrorContext


async def handle_data_transfer_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    message_pk = metadata.get("message_pk")
    if not message_pk:
        store.record_pending_call_result(
            message_id,
            metadata=metadata,
            success=False,
            error_code=error_code,
            error_description=description,
            error_details=details,
        )
        return True

    def _apply():
        message = (
            DataTransferMessage.objects.select_related("firmware_request")
            .filter(pk=message_pk)
            .first()
        )
        if not message:
            return
        status_value = (error_code or "Error").strip() or "Error"
        timestamp = timezone.now()
        message.status = status_value
        message.response_data = None
        message.error_code = (error_code or "").strip()
        message.error_description = (description or "").strip()
        message.error_details = details
        message.responded_at = timestamp
        message.save(
            update_fields=[
                "status",
                "response_data",
                "error_code",
                "error_description",
                "error_details",
                "responded_at",
                "updated_at",
            ]
        )
        request = getattr(message, "firmware_request", None)
        if request:
            request.status = status_value
            request.responded_at = timestamp
            request.response_payload = {
                "errorCode": error_code,
                "errorDescription": description,
                "details": details,
            }
            request.save(
                update_fields=[
                    "status",
                    "responded_at",
                    "response_payload",
                    "updated_at",
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

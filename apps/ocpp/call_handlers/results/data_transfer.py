"""Call result handlers for data transfer actions."""
from __future__ import annotations

from channels.db import database_sync_to_async
from django.utils import timezone

from ... import store
from ...models import DataTransferMessage
from ..types import CallResultContext


async def handle_data_transfer_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    message_pk = metadata.get("message_pk")
    if not message_pk:
        store.record_pending_call_result(
            message_id,
            metadata=metadata,
            payload=payload_data,
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
        status_value = str(payload_data.get("status") or "").strip()
        if not status_value:
            status_value = metadata.get("fallback_status") or "Unknown"
        timestamp = timezone.now()
        message.status = status_value
        message.response_data = (payload_data or {}).get("data")
        message.error_code = ""
        message.error_description = ""
        message.error_details = None
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
            request.response_payload = payload_data
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
        payload=payload_data,
    )
    return True

"""Call error handlers for schedule actions."""
from __future__ import annotations

from channels.db import database_sync_to_async
from django.utils import timezone

from ... import store
from ...models import PowerProjection
from ..types import CallErrorContext


async def handle_get_composite_schedule_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    projection_pk = metadata.get("projection_pk")

    def _apply_error():
        if not projection_pk:
            return
        projection = PowerProjection.objects.filter(pk=projection_pk).first()
        if not projection:
            return
        projection.status = error_code or "Error"
        projection.schedule_start = None
        projection.duration_seconds = None
        projection.charging_schedule_periods = []
        projection.raw_response = {
            "errorCode": error_code or "",
            "description": description or "",
            "details": details or {},
        }
        projection.received_at = timezone.now()
        projection.save(
            update_fields=[
                "status",
                "schedule_start",
                "duration_seconds",
                "charging_schedule_periods",
                "raw_response",
                "received_at",
                "updated_at",
            ]
        )

    await database_sync_to_async(_apply_error)()

    parts: list[str] = []
    if error_code:
        parts.append(f"code={error_code}")
    if description:
        parts.append(f"description={description}")
    message = "GetCompositeSchedule error"
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

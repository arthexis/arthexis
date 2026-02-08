"""Call error handlers for firmware actions."""
from __future__ import annotations

from channels.db import database_sync_to_async
from django.utils import timezone

from ... import store
from ...models import CPFirmwareDeployment
from ..types import CallErrorContext
from ..utils import _json_details


async def handle_update_firmware_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    deployment_pk = metadata.get("deployment_pk")

    def _apply():
        if not deployment_pk:
            return
        deployment = CPFirmwareDeployment.objects.filter(pk=deployment_pk).first()
        if not deployment:
            return
        parts: list[str] = []
        if error_code:
            parts.append(f"code={str(error_code).strip()}")
        if description:
            parts.append(f"description={str(description).strip()}")
        details_text = _json_details(details)
        if details_text:
            parts.append(f"details={details_text}")
        message = "UpdateFirmware error"
        if parts:
            message += ": " + ", ".join(parts)
        deployment.mark_status(
            "Error",
            message,
            timezone.now(),
            response=details or {},
        )
        deployment.completed_at = timezone.now()
        deployment.save(update_fields=["completed_at", "updated_at"])

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


async def handle_publish_firmware_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    deployment_pk = metadata.get("deployment_pk")

    def _apply():
        if not deployment_pk:
            return
        deployment = CPFirmwareDeployment.objects.filter(pk=deployment_pk).first()
        if not deployment:
            return
        parts: list[str] = []
        if error_code:
            parts.append(f"code={str(error_code).strip()}")
        if description:
            parts.append(f"description={str(description).strip()}")
        details_text = _json_details(details)
        if details_text:
            parts.append(f"details={details_text}")
        message = "PublishFirmware error"
        if parts:
            message += ": " + ", ".join(parts)
        deployment.mark_status(
            "Error",
            message,
            timezone.now(),
            response=details or {},
        )
        deployment.completed_at = timezone.now()
        deployment.save(update_fields=["completed_at", "updated_at"])

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


async def handle_unpublish_firmware_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    parts: list[str] = []
    if error_code:
        parts.append(f"code={str(error_code).strip()}")
    if description:
        parts.append(f"description={str(description).strip()}")
    details_text = _json_details(details)
    if details_text:
        parts.append(f"details={details_text}")
    message = "UnpublishFirmware error"
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

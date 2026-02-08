"""Call result handlers for firmware actions."""
from __future__ import annotations

from channels.db import database_sync_to_async
from django.utils import timezone

from ... import store
from ...models import CPFirmwareDeployment
from ..types import CallResultContext


async def handle_update_firmware_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    deployment_pk = metadata.get("deployment_pk")

    def _apply():
        if not deployment_pk:
            return
        deployment = CPFirmwareDeployment.objects.filter(pk=deployment_pk).first()
        if not deployment:
            return
        status_value = str(payload_data.get("status") or "").strip() or "Accepted"
        deployment.mark_status(
            status_value,
            "",
            timezone.now(),
            response=payload_data,
        )

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_publish_firmware_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    deployment_pk = metadata.get("deployment_pk")

    def _apply():
        if not deployment_pk:
            return
        deployment = CPFirmwareDeployment.objects.filter(pk=deployment_pk).first()
        if not deployment:
            return
        status_value = str(payload_data.get("status") or "").strip() or "Accepted"
        deployment.mark_status(
            status_value,
            "",
            timezone.now(),
            response=payload_data,
        )

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_unpublish_firmware_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "UnpublishFirmware result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True

from __future__ import annotations

import json

from channels.db import database_sync_to_async
from django.utils import timezone

from .. import store
from ..models import CPNetworkProfileDeployment, Charger
from ..utils import _parse_ocpp_timestamp
from .types import CallErrorContext, CallResultContext


async def handle_set_network_profile_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip() or "Accepted"
    timestamp_value = _parse_ocpp_timestamp(payload_data.get("timestamp"))
    deployment_pk = metadata.get("deployment_pk")
    status_timestamp = timestamp_value or timezone.now()

    def _apply():
        deployment = CPNetworkProfileDeployment.objects.select_related(
            "network_profile", "charger"
        ).filter(pk=deployment_pk)
        deployment_obj = deployment.first()
        if deployment_obj:
            deployment_obj.mark_status(
                status_value, "", status_timestamp, response=payload_data
            )
            deployment_obj.completed_at = timezone.now()
            deployment_obj.save(update_fields=["completed_at", "updated_at"])
            if status_value.casefold() == "accepted":
                Charger.objects.filter(pk=deployment_obj.charger_id).update(
                    network_profile=deployment_obj.network_profile
                )

    await database_sync_to_async(_apply)()
    message = "SetNetworkProfile result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_set_network_profile_error(
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
        deployment = CPNetworkProfileDeployment.objects.filter(pk=deployment_pk).first()
        if not deployment:
            return
        detail_text = (description or "").strip()
        if not detail_text and details:
            try:
                detail_text = json.dumps(details, sort_keys=True)
            except Exception:
                detail_text = str(details)
        if not detail_text:
            detail_text = (error_code or "").strip() or "Error"
        deployment.mark_status("Error", detail_text, response=details)
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

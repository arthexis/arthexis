"""Call result handlers for network profile actions."""
from __future__ import annotations

from channels.db import database_sync_to_async
from django.utils import timezone

from ... import store
from ...models import CPNetworkProfileDeployment, Charger
from ...utils import _parse_ocpp_timestamp
from ..types import CallResultContext


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

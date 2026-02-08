"""Call result handlers for charging profile actions."""
from __future__ import annotations

from channels.db import database_sync_to_async
from django.utils import timezone

from ... import store
from ...models import ChargingProfile
from ..types import CallResultContext
from ..utils import _format_status_info


async def handle_set_charging_profile_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "SetChargingProfile result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_clear_charging_profile_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    status_info = _format_status_info(payload_data.get("statusInfo"))
    message = "ClearChargingProfile result"
    parts: list[str] = []
    if status_value:
        parts.append(f"status={status_value}")
    if status_info:
        parts.append(f"info={status_info}")
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")

    charging_profile_id = metadata.get("charging_profile_id")
    charger_id = metadata.get("charger_id")
    responded_at = timezone.now()

    def _apply_response() -> None:
        if not charging_profile_id:
            return
        qs = ChargingProfile.objects.filter(charging_profile_id=charging_profile_id)
        if charger_id:
            qs = qs.filter(charger__charger_id=str(charger_id))
        qs.update(
            last_status=status_value,
            last_status_info=status_info,
            last_response_payload=payload_data,
            last_response_at=responded_at,
        )

    await database_sync_to_async(_apply_response)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_get_charging_profiles_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "GetChargingProfiles result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True

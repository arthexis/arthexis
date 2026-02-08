"""Call error handlers for charging profile actions."""
from __future__ import annotations

from channels.db import database_sync_to_async
from django.utils import timezone

from ... import store
from ...models import ChargingProfile
from ..types import CallErrorContext
from ..utils import _json_details


async def handle_set_charging_profile_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    parts: list[str] = []
    code_text = (error_code or "").strip()
    description_text = (description or "").strip()
    if code_text:
        parts.append(f"code={code_text}")
    if description_text:
        parts.append(f"description={description_text}")
    details_text = _json_details(details)
    if details_text:
        parts.append(f"details={details_text}")
    message = "SetChargingProfile error"
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


async def handle_clear_charging_profile_error(
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
        parts.append(f"code={error_code}")
    if description:
        parts.append(f"description={description}")
    message = "ClearChargingProfile error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")

    charging_profile_id = metadata.get("charging_profile_id")
    charger_id = metadata.get("charger_id")
    responded_at = timezone.now()
    error_payload = {
        "errorCode": error_code or "",
        "description": description or "",
        "details": details or {},
    }
    detail_text = (description or "").strip() or _json_details(details)
    if not detail_text:
        detail_text = (error_code or "").strip()

    def _apply_error() -> None:
        if not charging_profile_id:
            return
        qs = ChargingProfile.objects.filter(charging_profile_id=charging_profile_id)
        if charger_id:
            qs = qs.filter(charger__charger_id=str(charger_id))
        qs.update(
            last_status=error_code or "Error",
            last_status_info=detail_text,
            last_response_payload=error_payload,
            last_response_at=responded_at,
        )

    await database_sync_to_async(_apply_error)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_get_charging_profiles_error(
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
    message = "GetChargingProfiles error"
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

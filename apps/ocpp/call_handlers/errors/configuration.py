"""Call error handlers for configuration actions."""
from __future__ import annotations

from ... import store
from ..types import CallErrorContext
from ..utils import _json_details


async def handle_change_configuration_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    key_value = str(metadata.get("key") or "").strip()
    parts: list[str] = []
    if key_value:
        parts.append(f"key={key_value}")
    if error_code:
        parts.append(f"code={str(error_code).strip()}")
    if description:
        parts.append(f"description={str(description).strip()}")
    if details:
        parts.append(f"details={_json_details(details)}")
    message = "ChangeConfiguration error"
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


async def handle_get_configuration_error(
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
    if code_text:
        parts.append(f"code={code_text}")
    description_text = (description or "").strip()
    if description_text:
        parts.append(f"description={description_text}")
    details_text = _json_details(details)
    if details_text:
        parts.append(f"details={details_text}")
    if parts:
        message = "GetConfiguration error: " + ", ".join(parts)
    else:
        message = "GetConfiguration error"
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

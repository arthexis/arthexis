"""Call error handlers for trigger message actions."""
from __future__ import annotations

from ... import store
from ..types import CallErrorContext
from ..utils import _json_details


async def handle_trigger_message_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    target = metadata.get("trigger_target") or metadata.get("follow_up_action")
    connector_value = metadata.get("trigger_connector")
    parts: list[str] = []
    if error_code:
        parts.append(f"code={str(error_code).strip()}")
    if description:
        parts.append(f"description={str(description).strip()}")
    if details:
        parts.append("details=" + _json_details(details))
    label = f"TriggerMessage {target}" if target else "TriggerMessage"
    message = label + " error"
    if parts:
        message += ": " + ", ".join(parts)
    if connector_value:
        message += f", connector={connector_value}"
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

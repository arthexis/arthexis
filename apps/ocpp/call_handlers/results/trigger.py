"""Call result handlers for trigger message actions."""
from __future__ import annotations

from ... import store
from ..types import CallResultContext


async def handle_trigger_message_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    target = metadata.get("trigger_target") or metadata.get("follow_up_action")
    connector_value = metadata.get("trigger_connector")
    message = "TriggerMessage result"
    if target:
        message = f"TriggerMessage {target} result"
    if status_value:
        message += f": status={status_value}"
    if connector_value:
        message += f", connector={connector_value}"
    store.add_log(log_key, message, log_type="charger")
    if status_value == "Accepted" and target:
        store.register_triggered_followup(
            consumer.charger_id,
            str(target),
            connector=connector_value,
            log_key=log_key,
            target=str(target),
        )
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True

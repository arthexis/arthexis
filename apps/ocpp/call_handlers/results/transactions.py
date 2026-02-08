"""Call result handlers for transaction actions."""
from __future__ import annotations

from ... import store
from ..types import CallResultContext


async def handle_remote_start_transaction_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "RemoteStartTransaction result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_remote_stop_transaction_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "RemoteStopTransaction result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_request_start_transaction_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "RequestStartTransaction result"
    if status_value:
        message += f": status={status_value}"
    tx_identifier = payload_data.get("transactionId")
    if tx_identifier:
        message += f", transactionId={tx_identifier}"
    store.add_log(log_key, message, log_type="charger")
    status_label = status_value.casefold()
    request_status = "accepted" if status_label == "accepted" else "rejected"
    store.update_transaction_request(
        message_id,
        status=request_status,
        transaction_id=tx_identifier,
    )
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_request_stop_transaction_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "RequestStopTransaction result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    status_label = status_value.casefold()
    request_status = "accepted" if status_label == "accepted" else "rejected"
    store.update_transaction_request(
        message_id,
        status=request_status,
        transaction_id=metadata.get("transaction_id"),
    )
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_get_transaction_status_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    ongoing = payload_data.get("ongoingIndicator")
    messages_in_queue = payload_data.get("messagesInQueue")
    parts: list[str] = []
    if ongoing is not None:
        parts.append(f"ongoingIndicator={ongoing}")
    if messages_in_queue is not None:
        parts.append(f"messagesInQueue={messages_in_queue}")
    message = "GetTransactionStatus result"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True

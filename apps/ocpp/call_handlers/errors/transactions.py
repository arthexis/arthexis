"""Call error handlers for transaction actions."""
from __future__ import annotations

from ... import store
from ..types import CallErrorContext


async def handle_remote_start_transaction_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    message = "RemoteStartTransaction error"
    if error_code:
        message += f": code={str(error_code).strip()}"
    if description:
        suffix = str(description).strip()
        if suffix:
            message += f", description={suffix}"
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


async def handle_remote_stop_transaction_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    message = "RemoteStopTransaction error"
    if error_code:
        message += f": code={str(error_code).strip()}"
    if description:
        suffix = str(description).strip()
        if suffix:
            message += f", description={suffix}"
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


async def handle_request_start_transaction_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    message = "RequestStartTransaction error"
    if error_code:
        message += f": code={str(error_code).strip()}"
    if description:
        suffix = str(description).strip()
        if suffix:
            message += f", description={suffix}"
    store.add_log(log_key, message, log_type="charger")
    store.update_transaction_request(message_id, status="rejected")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_request_stop_transaction_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    message = "RequestStopTransaction error"
    if error_code:
        message += f": code={str(error_code).strip()}"
    if description:
        suffix = str(description).strip()
        if suffix:
            message += f", description={suffix}"
    store.add_log(log_key, message, log_type="charger")
    store.update_transaction_request(message_id, status="rejected")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_get_transaction_status_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    message = "GetTransactionStatus error"
    if error_code:
        message += f": code={str(error_code).strip()}"
    if description:
        suffix = str(description).strip()
        if suffix:
            message += f", description={suffix}"
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

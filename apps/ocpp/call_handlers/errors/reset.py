"""Call error handlers for reset actions."""
from __future__ import annotations

from ... import store
from ..types import CallErrorContext


async def handle_reset_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    message = "Reset error"
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

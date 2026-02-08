"""Call error handlers for diagnostics actions."""
from __future__ import annotations

from ... import store
from ..types import CallErrorContext
from ..utils import _json_details


async def handle_get_diagnostics_error(
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
    message = "GetDiagnostics error"
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

"""Call error handlers for monitoring actions."""
from __future__ import annotations

from ... import store
from ..types import CallErrorContext
from ..utils import _json_details


async def handle_set_monitoring_base_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    monitoring_base = metadata.get("monitoring_base")
    fragments: list[str] = []
    if error_code:
        fragments.append(f"code={str(error_code).strip()}")
    if description:
        fragments.append(f"description={str(description).strip()}")
    details_text = _json_details(details)
    if details_text:
        fragments.append(f"details={details_text}")
    if monitoring_base not in (None, ""):
        fragments.append(f"base={monitoring_base}")

    message = "SetMonitoringBase error"
    if fragments:
        message += ": " + ", ".join(fragments)
    store.add_log(log_key, message, log_type="charger")

    result_metadata = dict(metadata or {})
    if monitoring_base not in (None, ""):
        result_metadata["monitoring_base"] = monitoring_base

    store.record_pending_call_result(
        message_id,
        metadata=result_metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_set_monitoring_level_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    monitoring_level = metadata.get("monitoring_level")
    fragments: list[str] = []
    if error_code:
        fragments.append(f"code={str(error_code).strip()}")
    if description:
        fragments.append(f"description={str(description).strip()}")
    details_text = _json_details(details)
    if details_text:
        fragments.append(f"details={details_text}")
    if monitoring_level not in (None, ""):
        fragments.append(f"severity={monitoring_level}")

    message = "SetMonitoringLevel error"
    if fragments:
        message += ": " + ", ".join(fragments)
    store.add_log(log_key, message, log_type="charger")

    result_metadata = dict(metadata or {})
    if monitoring_level not in (None, ""):
        result_metadata["monitoring_level"] = monitoring_level

    store.record_pending_call_result(
        message_id,
        metadata=result_metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True

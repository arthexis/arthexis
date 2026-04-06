"""Handlers for configuration, control and reporting call errors."""

from __future__ import annotations


from channels.db import database_sync_to_async
from django.utils import timezone

from apps.ocpp import store
from apps.ocpp.models import CPNetworkProfileDeployment

from .common import _json_details
from .types import CallErrorContext


async def handle_change_configuration_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle ChangeConfiguration errors."""
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
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_clear_cache_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle ClearCache errors."""
    parts: list[str] = []
    if (code_text := (error_code or "").strip()):
        parts.append(f"code={code_text}")
    if (description_text := (description or "").strip()):
        parts.append(f"description={description_text}")
    if (details_text := _json_details(details)):
        parts.append(f"details={details_text}")
    message = "ClearCache error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    await consumer._update_local_authorization_state(None)
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_send_local_list_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle SendLocalList errors."""
    parts: list[str] = []
    if (code_text := (error_code or "").strip()):
        parts.append(f"code={code_text}")
    if (description_text := (description or "").strip()):
        parts.append(f"description={description_text}")
    if (details_text := _json_details(details)):
        parts.append(f"details={details_text}")
    message = "SendLocalList error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_get_local_list_version_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle GetLocalListVersion errors."""
    parts: list[str] = []
    if (code_text := (error_code or "").strip()):
        parts.append(f"code={code_text}")
    if (description_text := (description or "").strip()):
        parts.append(f"description={description_text}")
    if (details_text := _json_details(details)):
        parts.append(f"details={details_text}")
    message = "GetLocalListVersion error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_get_configuration_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle GetConfiguration errors."""
    parts = []
    if (code_text := (error_code or "").strip()):
        parts.append(f"code={code_text}")
    if (description_text := (description or "").strip()):
        parts.append(f"description={description_text}")
    if (details_text := _json_details(details)):
        parts.append(f"details={details_text}")
    message = "GetConfiguration error" + (": " + ", ".join(parts) if parts else "")
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_get_variables_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle GetVariables errors."""
    parts: list[str] = []
    if (code_text := (error_code or "").strip()):
        parts.append(f"code={code_text}")
    if (description_text := (description or "").strip()):
        parts.append(f"description={description_text}")
    if (details_text := _json_details(details)):
        parts.append(f"details={details_text}")
    message = "GetVariables error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_set_variables_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle SetVariables errors."""
    parts: list[str] = []
    if (code_text := (error_code or "").strip()):
        parts.append(f"code={code_text}")
    if (description_text := (description or "").strip()):
        parts.append(f"description={description_text}")
    if (details_text := _json_details(details)):
        parts.append(f"details={details_text}")
    message = "SetVariables error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_trigger_message_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle TriggerMessage errors."""
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
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_remote_start_transaction_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle RemoteStartTransaction errors."""
    parts: list[str] = []
    if error_code and (code_text := str(error_code).strip()):
        parts.append(f"code={code_text}")
    if description and (description_text := str(description).strip()):
        parts.append(f"description={description_text}")
    message = "RemoteStartTransaction error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_remote_stop_transaction_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle RemoteStopTransaction errors."""
    parts: list[str] = []
    if error_code and (code_text := str(error_code).strip()):
        parts.append(f"code={code_text}")
    if description and (description_text := str(description).strip()):
        parts.append(f"description={description_text}")
    message = "RemoteStopTransaction error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_request_start_transaction_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle RequestStartTransaction errors."""
    parts: list[str] = []
    if error_code and (code_text := str(error_code).strip()):
        parts.append(f"code={code_text}")
    if description and (description_text := str(description).strip()):
        parts.append(f"description={description_text}")
    message = "RequestStartTransaction error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    store.update_transaction_request(message_id, status="rejected")
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_request_stop_transaction_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle RequestStopTransaction errors."""
    parts: list[str] = []
    if error_code and (code_text := str(error_code).strip()):
        parts.append(f"code={code_text}")
    if description and (description_text := str(description).strip()):
        parts.append(f"description={description_text}")
    message = "RequestStopTransaction error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    store.update_transaction_request(message_id, status="rejected")
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_get_transaction_status_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle GetTransactionStatus errors."""
    parts: list[str] = []
    if error_code and (code_text := str(error_code).strip()):
        parts.append(f"code={code_text}")
    if description and (description_text := str(description).strip()):
        parts.append(f"description={description_text}")
    message = "GetTransactionStatus error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_reset_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle Reset errors."""
    parts: list[str] = []
    if error_code and (code_text := str(error_code).strip()):
        parts.append(f"code={code_text}")
    if description and (description_text := str(description).strip()):
        parts.append(f"description={description_text}")
    message = "Reset error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_change_availability_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle ChangeAvailability errors."""
    detail_text = _json_details(details) if details is not None else ""
    if not detail_text:
        detail_text = (description or "").strip() or (error_code or "").strip() or "Error"
    await consumer._update_change_availability_state(
        metadata.get("connector_id"),
        metadata.get("availability_type"),
        "Rejected",
        metadata.get("requested_at"),
        details=detail_text,
    )
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_unlock_connector_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle UnlockConnector errors."""
    detail_text = _json_details(details) if details is not None else ""
    if not detail_text:
        detail_text = (description or "").strip() or (error_code or "").strip() or "Error"
    await consumer._update_change_availability_state(metadata.get("connector_id"), None, "Rejected", metadata.get("requested_at"), details=detail_text)

    parts: list[str] = []
    if error_code:
        parts.append(f"code={error_code}")
    if description:
        parts.append(f"description={description}")
    if details:
        parts.append(f"details={_json_details(details)}")
    message = "UnlockConnector error" + ((": " + ", ".join(parts)) if parts else "")
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_clear_display_message_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle ClearDisplayMessage errors."""
    parts: list[str] = []
    if error_code:
        parts.append(f"code={str(error_code).strip()}")
    if description:
        parts.append(f"description={str(description).strip()}")
    if (details_text := _json_details(details)):
        parts.append(f"details={details_text}")
    message = "ClearDisplayMessage error" + ((": " + ", ".join(parts)) if parts else "")
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_customer_information_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle CustomerInformation errors."""
    parts: list[str] = []
    if error_code:
        parts.append(f"code={str(error_code).strip()}")
    if description:
        parts.append(f"description={str(description).strip()}")
    if (details_text := _json_details(details)):
        parts.append(f"details={details_text}")
    message = "CustomerInformation error" + ((": " + ", ".join(parts)) if parts else "")
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_get_base_report_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle GetBaseReport errors."""
    parts: list[str] = []
    if error_code:
        parts.append(f"code={str(error_code).strip()}")
    if description:
        parts.append(f"description={str(description).strip()}")
    if (details_text := _json_details(details)):
        parts.append(f"details={details_text}")
    message = "GetBaseReport error" + ((": " + ", ".join(parts)) if parts else "")
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_get_display_messages_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle GetDisplayMessages errors."""
    parts: list[str] = []
    if error_code:
        parts.append(f"code={str(error_code).strip()}")
    if description:
        parts.append(f"description={str(description).strip()}")
    if (details_text := _json_details(details)):
        parts.append(f"details={details_text}")
    message = "GetDisplayMessages error" + ((": " + ", ".join(parts)) if parts else "")
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_get_report_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle GetReport errors."""
    parts: list[str] = []
    if error_code:
        parts.append(f"code={str(error_code).strip()}")
    if description:
        parts.append(f"description={str(description).strip()}")
    if (details_text := _json_details(details)):
        parts.append(f"details={details_text}")
    message = "GetReport error" + ((": " + ", ".join(parts)) if parts else "")
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_set_monitoring_base_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle SetMonitoringBase errors."""
    monitoring_base = metadata.get("monitoring_base")
    fragments: list[str] = []
    if error_code:
        fragments.append(f"code={str(error_code).strip()}")
    if description:
        fragments.append(f"description={str(description).strip()}")
    if (details_text := _json_details(details)):
        fragments.append(f"details={details_text}")
    if monitoring_base not in (None, ""):
        fragments.append(f"base={monitoring_base}")
    message = "SetMonitoringBase error" + ((": " + ", ".join(fragments)) if fragments else "")
    store.add_log(log_key, message, log_type="charger")
    result_metadata = dict(metadata or {})
    if monitoring_base not in (None, ""):
        result_metadata["monitoring_base"] = monitoring_base
    store.record_pending_call_result(message_id, metadata=result_metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_set_monitoring_level_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle SetMonitoringLevel errors."""
    monitoring_level = metadata.get("monitoring_level")
    fragments: list[str] = []
    if error_code:
        fragments.append(f"code={str(error_code).strip()}")
    if description:
        fragments.append(f"description={str(description).strip()}")
    if (details_text := _json_details(details)):
        fragments.append(f"details={details_text}")
    if monitoring_level not in (None, ""):
        fragments.append(f"severity={monitoring_level}")
    message = "SetMonitoringLevel error" + ((": " + ", ".join(fragments)) if fragments else "")
    store.add_log(log_key, message, log_type="charger")
    result_metadata = dict(metadata or {})
    if monitoring_level not in (None, ""):
        result_metadata["monitoring_level"] = monitoring_level
    store.record_pending_call_result(message_id, metadata=result_metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_set_variable_monitoring_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle SetVariableMonitoring errors."""
    parts: list[str] = []
    if (code_text := (error_code or "").strip()):
        parts.append(f"code={code_text}")
    if (description_text := (description or "").strip()):
        parts.append(f"description={description_text}")
    if (details_text := _json_details(details)):
        parts.append(f"details={details_text}")
    message = "SetVariableMonitoring error" + ((": " + ", ".join(parts)) if parts else "")
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_clear_variable_monitoring_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle ClearVariableMonitoring errors."""
    parts: list[str] = []
    if (code_text := (error_code or "").strip()):
        parts.append(f"code={code_text}")
    if (description_text := (description or "").strip()):
        parts.append(f"description={description_text}")
    if (details_text := _json_details(details)):
        parts.append(f"details={details_text}")
    message = "ClearVariableMonitoring error" + ((": " + ", ".join(parts)) if parts else "")
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_get_monitoring_report_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle GetMonitoringReport errors."""
    parts: list[str] = []
    if (code_text := (error_code or "").strip()):
        parts.append(f"code={code_text}")
    if (description_text := (description or "").strip()):
        parts.append(f"description={description_text}")
    if (details_text := _json_details(details)):
        parts.append(f"details={details_text}")
    message = "GetMonitoringReport error" + ((": " + ", ".join(parts)) if parts else "")
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_set_display_message_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle SetDisplayMessage errors."""
    parts: list[str] = []
    if error_code:
        parts.append(f"code={str(error_code).strip()}")
    if description:
        parts.append(f"description={str(description).strip()}")
    if (details_text := _json_details(details)):
        parts.append(f"details={details_text}")
    message = "SetDisplayMessage error" + ((": " + ", ".join(parts)) if parts else "")
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True


async def handle_set_network_profile_error(consumer: CallErrorContext, message_id: str, metadata: dict, error_code: str | None, description: str | None, details: dict | None, log_key: str) -> bool:
    """Handle SetNetworkProfile errors."""
    deployment_pk = metadata.get("deployment_pk")

    def _apply() -> None:
        deployment = CPNetworkProfileDeployment.objects.filter(pk=deployment_pk).first()
        if not deployment:
            return
        detail_text = (description or "").strip()
        if not detail_text:
            detail_text = _json_details(details)
        if not detail_text:
            detail_text = (error_code or "").strip() or "Error"
        deployment.mark_status("Error", detail_text, response=details)
        deployment.completed_at = timezone.now()
        deployment.save(update_fields=["completed_at", "updated_at"])

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(message_id, metadata=metadata, success=False, error_code=error_code, error_description=description, error_details=details)
    return True

from __future__ import annotations

import json
from typing import Awaitable, Callable, Protocol

from channels.db import database_sync_to_async
from django.utils import timezone

from . import store
from .models import (
    CPFirmwareDeployment,
    CPReservation,
    ChargerLogRequest,
    DataTransferMessage,
    PowerProjection,
)

class CallErrorContext(Protocol):
    charger_id: str | None
    store_key: str

    async def _update_local_authorization_state(self, version: int | None) -> None:
        ...

    async def _update_change_availability_state(
        self,
        connector_value: int | None,
        requested_type: str | None,
        status: str,
        requested_at,
        *,
        details: str = "",
    ) -> None:
        ...


CallErrorHandler = Callable[
    [CallErrorContext, str, dict, str | None, str | None, dict | None, str],
    Awaitable[bool],
]


def _json_details(details: dict | None) -> str:
    if not details:
        return ""
    try:
        return json.dumps(details, sort_keys=True, ensure_ascii=False)
    except TypeError:
        return str(details)


async def handle_get_composite_schedule_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    projection_pk = metadata.get("projection_pk")

    def _apply_error():
        if not projection_pk:
            return
        projection = PowerProjection.objects.filter(pk=projection_pk).first()
        if not projection:
            return
        projection.status = error_code or "Error"
        projection.schedule_start = None
        projection.duration_seconds = None
        projection.charging_schedule_periods = []
        projection.raw_response = {
            "errorCode": error_code or "",
            "description": description or "",
            "details": details or {},
        }
        projection.received_at = timezone.now()
        projection.save(
            update_fields=[
                "status",
                "schedule_start",
                "duration_seconds",
                "charging_schedule_periods",
                "raw_response",
                "received_at",
                "updated_at",
            ]
        )

    await database_sync_to_async(_apply_error)()

    parts: list[str] = []
    if error_code:
        parts.append(f"code={error_code}")
    if description:
        parts.append(f"description={description}")
    message = "GetCompositeSchedule error"
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


async def handle_get_log_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    request_pk = metadata.get("log_request_pk")
    capture_key = metadata.get("capture_key")

    def _apply_error() -> None:
        if not request_pk:
            return
        request = ChargerLogRequest.objects.filter(pk=request_pk).first()
        if not request:
            return
        label = (error_code or "Error").strip() or "Error"
        request.status = label
        request.responded_at = timezone.now()
        request.raw_response = {
            "errorCode": error_code,
            "errorDescription": description,
            "details": details,
        }
        if capture_key:
            request.session_key = str(capture_key)
        request.save(
            update_fields=[
                "status",
                "responded_at",
                "raw_response",
                "session_key",
            ]
        )

    await database_sync_to_async(_apply_error)()
    parts: list[str] = []
    if error_code:
        parts.append(f"code={error_code}")
    if description:
        parts.append(f"description={description}")
    message = "GetLog error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    if capture_key:
        store.finalize_log_capture(str(capture_key))
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_data_transfer_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    message_pk = metadata.get("message_pk")
    if not message_pk:
        store.record_pending_call_result(
            message_id,
            metadata=metadata,
            success=False,
            error_code=error_code,
            error_description=description,
            error_details=details,
        )
        return True

    def _apply():
        message = (
            DataTransferMessage.objects.select_related("firmware_request")
            .filter(pk=message_pk)
            .first()
        )
        if not message:
            return
        status_value = (error_code or "Error").strip() or "Error"
        timestamp = timezone.now()
        message.status = status_value
        message.response_data = None
        message.error_code = (error_code or "").strip()
        message.error_description = (description or "").strip()
        message.error_details = details
        message.responded_at = timestamp
        message.save(
            update_fields=[
                "status",
                "response_data",
                "error_code",
                "error_description",
                "error_details",
                "responded_at",
                "updated_at",
            ]
        )
        request = getattr(message, "firmware_request", None)
        if request:
            request.status = status_value
            request.responded_at = timestamp
            request.response_payload = {
                "errorCode": error_code,
                "errorDescription": description,
                "details": details,
            }
            request.save(
                update_fields=[
                    "status",
                    "responded_at",
                    "response_payload",
                    "updated_at",
                ]
            )

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_clear_cache_error(
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
    message = "ClearCache error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    await consumer._update_local_authorization_state(None)
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


async def handle_update_firmware_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    deployment_pk = metadata.get("deployment_pk")

    def _apply():
        if not deployment_pk:
            return
        deployment = CPFirmwareDeployment.objects.filter(pk=deployment_pk).first()
        if not deployment:
            return
        parts: list[str] = []
        if error_code:
            parts.append(f"code={str(error_code).strip()}")
        if description:
            parts.append(f"description={str(description).strip()}")
        details_text = _json_details(details)
        if details_text:
            parts.append(f"details={details_text}")
        message = "UpdateFirmware error"
        if parts:
            message += ": " + ", ".join(parts)
        deployment.mark_status(
            "Error",
            message,
            timezone.now(),
            response=details or {},
        )
        deployment.completed_at = timezone.now()
        deployment.save(update_fields=["completed_at", "updated_at"])

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_reserve_now_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    parts: list[str] = []
    code_text = (error_code or "").strip() if error_code else ""
    if code_text:
        parts.append(f"code={code_text}")
    description_text = (description or "").strip() if description else ""
    if description_text:
        parts.append(f"description={description_text}")
    details_text = _json_details(details)
    if details_text:
        parts.append(f"details={details_text}")
    message = "ReserveNow error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")

    reservation_pk = metadata.get("reservation_pk")

    def _apply():
        if not reservation_pk:
            return
        reservation = CPReservation.objects.filter(pk=reservation_pk).first()
        if not reservation:
            return
        summary_parts = []
        if code_text:
            summary_parts.append(code_text)
        if description_text:
            summary_parts.append(description_text)
        if details_text:
            summary_parts.append(details_text)
        reservation.evcs_status = ""
        reservation.evcs_error = "; ".join(summary_parts)
        reservation.evcs_confirmed = False
        reservation.evcs_confirmed_at = None
        reservation.save(
            update_fields=[
                "evcs_status",
                "evcs_error",
                "evcs_confirmed",
                "evcs_confirmed_at",
                "updated_on",
            ]
        )

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_cancel_reservation_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    parts: list[str] = []
    code_text = (error_code or "").strip() if error_code else ""
    if code_text:
        parts.append(f"code={code_text}")
    description_text = (description or "").strip() if description else ""
    if description_text:
        parts.append(f"description={description_text}")
    details_text = _json_details(details)
    if details_text:
        parts.append(f"details={details_text}")
    message = "CancelReservation error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")

    reservation_pk = metadata.get("reservation_pk")

    def _apply():
        if not reservation_pk:
            return
        reservation = CPReservation.objects.filter(pk=reservation_pk).first()
        if not reservation:
            return
        summary_parts = []
        if code_text:
            summary_parts.append(code_text)
        if description_text:
            summary_parts.append(description_text)
        if details_text:
            summary_parts.append(details_text)
        reservation.evcs_status = ""
        reservation.evcs_error = "; ".join(summary_parts)
        reservation.evcs_confirmed = False
        reservation.evcs_confirmed_at = None
        reservation.save(
            update_fields=[
                "evcs_status",
                "evcs_error",
                "evcs_confirmed",
                "evcs_confirmed_at",
                "updated_on",
            ]
        )

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


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


async def handle_change_availability_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    detail_text = (description or "").strip()
    if not detail_text and details:
        try:
            detail_text = json.dumps(details, sort_keys=True)
        except Exception:
            detail_text = str(details)
    if not detail_text:
        detail_text = (error_code or "").strip() or "Error"
    requested_type = metadata.get("availability_type")
    connector_value = metadata.get("connector_id")
    requested_at = metadata.get("requested_at")
    await consumer._update_change_availability_state(
        connector_value,
        requested_type,
        "Rejected",
        requested_at,
        details=detail_text,
    )
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


CALL_ERROR_HANDLERS: dict[str, CallErrorHandler] = {
    "GetCompositeSchedule": handle_get_composite_schedule_error,
    "ChangeConfiguration": handle_change_configuration_error,
    "GetLog": handle_get_log_error,
    "DataTransfer": handle_data_transfer_error,
    "ClearCache": handle_clear_cache_error,
    "GetConfiguration": handle_get_configuration_error,
    "TriggerMessage": handle_trigger_message_error,
    "UpdateFirmware": handle_update_firmware_error,
    "ReserveNow": handle_reserve_now_error,
    "CancelReservation": handle_cancel_reservation_error,
    "RemoteStartTransaction": handle_remote_start_transaction_error,
    "RemoteStopTransaction": handle_remote_stop_transaction_error,
    "Reset": handle_reset_error,
    "ChangeAvailability": handle_change_availability_error,
}


async def dispatch_call_error(
    consumer: CallErrorContext,
    action: str | None,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    if not action:
        return False
    handler = CALL_ERROR_HANDLERS.get(action)
    if not handler:
        return False
    return await handler(consumer, message_id, metadata, error_code, description, details, log_key)

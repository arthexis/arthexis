"""Firmware and diagnostics related error handlers."""

from __future__ import annotations

from channels.db import database_sync_to_async
from django.utils import timezone

from apps.ocpp import store
from apps.ocpp.models import CPFirmwareDeployment, ChargerLogRequest, PowerProjection

from .common import _json_details
from .types import CallErrorContext


async def handle_get_composite_schedule_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    """Handle errors for GetCompositeSchedule calls."""
    projection_pk = metadata.get("projection_pk")

    def _apply_error() -> None:
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


async def handle_get_log_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    """Handle GetLog call errors."""
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
        request.save(update_fields=["status", "responded_at", "raw_response", "session_key"])

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


async def handle_update_firmware_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    """Update firmware deployment state on call error."""
    deployment_pk = metadata.get("deployment_pk")

    def _apply() -> None:
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
        deployment.mark_status("Error", message, timezone.now(), response=details or {})
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


async def handle_publish_firmware_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    """Handle PublishFirmware errors."""
    deployment_pk = metadata.get("deployment_pk")

    def _apply() -> None:
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
        message = "PublishFirmware error"
        if parts:
            message += ": " + ", ".join(parts)
        deployment.mark_status("Error", message, timezone.now(), response=details or {})
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


async def handle_unpublish_firmware_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    """Handle UnpublishFirmware errors."""
    parts: list[str] = []
    if error_code:
        parts.append(f"code={str(error_code).strip()}")
    if description:
        parts.append(f"description={str(description).strip()}")
    details_text = _json_details(details)
    if details_text:
        parts.append(f"details={details_text}")
    message = "UnpublishFirmware error"
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


async def handle_get_diagnostics_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    """Handle GetDiagnostics errors."""
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

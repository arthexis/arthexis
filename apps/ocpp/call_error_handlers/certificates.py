"""Handlers for certificate management call errors."""

from __future__ import annotations

from channels.db import database_sync_to_async
from django.utils import timezone

from apps.ocpp import store
from apps.ocpp.models import CertificateOperation, InstalledCertificate

from .common import _json_details
from .types import CallErrorContext


def _apply_operation_error(
    operation_pk: int | None,
    responded_at,
    error_code: str | None,
    description: str | None,
    details: dict | None,
) -> None:
    operation = CertificateOperation.objects.filter(pk=operation_pk).first()
    if not operation:
        return
    detail_text = (description or "").strip() or _json_details(details)
    if not detail_text:
        detail_text = (error_code or "").strip() or "Error"
    operation.status = CertificateOperation.STATUS_ERROR
    operation.status_info = detail_text
    operation.response_payload = {
        "errorCode": error_code or "",
        "description": description or "",
        "details": details or {},
    }
    operation.responded_at = responded_at
    operation.save(update_fields=["status", "status_info", "response_payload", "responded_at"])


async def handle_install_certificate_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    """Handle InstallCertificate errors."""
    operation_pk = metadata.get("operation_pk")
    installed_pk = metadata.get("installed_certificate_pk")
    responded_at = timezone.now()

    def _apply() -> None:
        _apply_operation_error(operation_pk, responded_at, error_code, description, details)
        installed = InstalledCertificate.objects.filter(pk=installed_pk).first()
        if installed:
            installed.status = InstalledCertificate.STATUS_ERROR
            installed.last_action = CertificateOperation.ACTION_INSTALL
            installed.save(update_fields=["status", "last_action"])

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


async def handle_delete_certificate_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    """Handle DeleteCertificate errors."""
    operation_pk = metadata.get("operation_pk")
    installed_pk = metadata.get("installed_certificate_pk")
    responded_at = timezone.now()

    def _apply() -> None:
        _apply_operation_error(operation_pk, responded_at, error_code, description, details)
        installed = InstalledCertificate.objects.filter(pk=installed_pk).first()
        if installed:
            installed.status = InstalledCertificate.STATUS_ERROR
            installed.last_action = CertificateOperation.ACTION_DELETE
            installed.save(update_fields=["status", "last_action"])

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


async def handle_certificate_signed_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    """Handle CertificateSigned errors."""
    responded_at = timezone.now()
    operation_pk = metadata.get("operation_pk")

    await database_sync_to_async(_apply_operation_error)(
        operation_pk, responded_at, error_code, description, details
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


async def handle_get_installed_certificate_ids_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    """Handle GetInstalledCertificateIds errors."""
    responded_at = timezone.now()
    operation_pk = metadata.get("operation_pk")

    await database_sync_to_async(_apply_operation_error)(
        operation_pk, responded_at, error_code, description, details
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

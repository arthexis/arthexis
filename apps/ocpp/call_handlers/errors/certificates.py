"""Call error handlers for certificate actions."""
from __future__ import annotations

from channels.db import database_sync_to_async
from django.utils import timezone

from ... import store
from ...models import CertificateOperation, InstalledCertificate
from ..types import CallErrorContext
from ..utils import _json_details


async def handle_install_certificate_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    operation_pk = metadata.get("operation_pk")
    installed_pk = metadata.get("installed_certificate_pk")
    responded_at = timezone.now()

    def _apply():
        detail_text = (description or "").strip() or _json_details(details)
        if not detail_text:
            detail_text = (error_code or "").strip() or "Error"
        operation = CertificateOperation.objects.filter(pk=operation_pk).first()
        if operation:
            operation.status = CertificateOperation.STATUS_ERROR
            operation.status_info = detail_text
            operation.response_payload = {
                "errorCode": error_code or "",
                "description": description or "",
                "details": details or {},
            }
            operation.responded_at = responded_at
            operation.save(
                update_fields=["status", "status_info", "response_payload", "responded_at"]
            )
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
    operation_pk = metadata.get("operation_pk")
    installed_pk = metadata.get("installed_certificate_pk")
    responded_at = timezone.now()

    def _apply():
        detail_text = (description or "").strip() or _json_details(details)
        if not detail_text:
            detail_text = (error_code or "").strip() or "Error"
        operation = CertificateOperation.objects.filter(pk=operation_pk).first()
        if operation:
            operation.status = CertificateOperation.STATUS_ERROR
            operation.status_info = detail_text
            operation.response_payload = {
                "errorCode": error_code or "",
                "description": description or "",
                "details": details or {},
            }
            operation.responded_at = responded_at
            operation.save(
                update_fields=["status", "status_info", "response_payload", "responded_at"]
            )
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
    operation_pk = metadata.get("operation_pk")
    responded_at = timezone.now()

    def _apply():
        detail_text = (description or "").strip() or _json_details(details)
        if not detail_text:
            detail_text = (error_code or "").strip() or "Error"
        operation = CertificateOperation.objects.filter(pk=operation_pk).first()
        if operation:
            operation.status = CertificateOperation.STATUS_ERROR
            operation.status_info = detail_text
            operation.response_payload = {
                "errorCode": error_code or "",
                "description": description or "",
                "details": details or {},
            }
            operation.responded_at = responded_at
            operation.save(
                update_fields=["status", "status_info", "response_payload", "responded_at"]
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


async def handle_get_installed_certificate_ids_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    operation_pk = metadata.get("operation_pk")
    responded_at = timezone.now()

    def _apply():
        detail_text = (description or "").strip() or _json_details(details)
        if not detail_text:
            detail_text = (error_code or "").strip() or "Error"
        operation = CertificateOperation.objects.filter(pk=operation_pk).first()
        if operation:
            operation.status = CertificateOperation.STATUS_ERROR
            operation.status_info = detail_text
            operation.response_payload = {
                "errorCode": error_code or "",
                "description": description or "",
                "details": details or {},
            }
            operation.responded_at = responded_at
            operation.save(
                update_fields=["status", "status_info", "response_payload", "responded_at"]
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
